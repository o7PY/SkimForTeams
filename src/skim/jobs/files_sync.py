"""FR-203/FR-204: Files-tab metadata for tracked channel folders. Metadata only; file bytes are never fetched.

Paths are relative to the channel's Files folder and are built from the parent-item chain, because the
filesFolder item (and delta items) carry no usable parentReference.path (P2-DIFF-001, P2-D-121).
Event `at` is when the sync observed the change; the document row holds the real Graph timestamps (P2-D-125).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Awaitable, Callable

from ..graph.client import GraphError, GraphGone, GraphNotFound
from ..normalize import parse_ts
from ..normalize_graph import drive_item_row, join_path
from .common import JobContext, store_raw

log = logging.getLogger("skim.jobs")


# ---------------------------------------------------------------------------- applying one item
async def apply_item(ctx: JobContext, channel: dict, item: dict, path: str, raw_id: int, ts: datetime) -> int:
    """Upsert one live driveItem and write the document_events it implies. Returns the document id."""
    st, store = ctx.stats, ctx.storage
    row = drive_item_row(item, channel["files_drive_id"])
    row.update(team_id=channel["team_id"], channel_id=channel["id"], path=path, graph_raw_id=raw_id,
               deleted_at=None, last_seen_at=ts)
    old = await store.get_document(row["drive_id"], row["item_id"])
    events: list[dict] = []
    if old is None:
        events.append({"event": "created", "new_name": row["name"], "new_path": path})
    else:
        was_gone = old["deleted_at"] is not None
        if was_gone or old["channel_id"] != channel["id"]:
            events.append({"event": "restored" if was_gone else "moved", "old_name": old["name"], "new_name": row["name"],
                           "old_path": old["path"], "new_path": path})
        else:
            renamed = old["name"] != row["name"]
            moved = old["parent_item_id"] != row["parent_item_id"]
            if renamed:
                events.append({"event": "renamed", "old_name": old["name"], "new_name": row["name"],
                               "old_path": old["path"], "new_path": path})
            if moved:
                events.append({"event": "moved", "old_name": old["name"], "new_name": row["name"],
                               "old_path": old["path"], "new_path": path})
            # folders change their eTag whenever a child changes: not a "modified" signal
            if not (renamed or moved) and not row["is_folder"] and old["etag"] != row["etag"]:
                events.append({"event": "modified", "old_name": old["name"], "new_name": row["name"],
                               "old_path": old["path"], "new_path": path})
    doc_id = await store.upsert_document(row)
    for ev in events:
        await store.add_document_event({"document_id": doc_id, "at": ts, "graph_raw_id": raw_id, **ev})
        st[ev["event"]] += 1
    if not events:
        st["unchanged"] += 1
    st["items_seen"] += 1
    if old is not None and row["is_folder"] and old["path"] != path:
        await rewrite_descendants(ctx, row["drive_id"], row["item_id"], path, ts)
    return doc_id


async def rewrite_descendants(ctx: JobContext, drive_id: str, folder_item_id: str, folder_path: str, ts: datetime) -> None:
    """A folder rename/move changes every descendant's relative path; Graph will not list them. No events for
    descendants: their own name and parent did not change (P2-D-125)."""
    queue = [(folder_item_id, folder_path)]
    while queue:
        fid, base = queue.pop(0)
        for child in await ctx.storage.list_child_documents(drive_id, fid):
            new_path = join_path(base, child["name"])
            if child["path"] != new_path:
                await ctx.storage.upsert_document({**child, "path": new_path, "last_seen_at": ts})
            if child["is_folder"]:
                queue.append((child["item_id"], new_path))


# ---------------------------------------------------------------------------- initial delta link
async def _start_delta_link(ctx: JobContext, channel: dict, force_root: bool = False) -> tuple[str, str]:
    """A deltaLink for "now", taken BEFORE listing so changes made during the listing are replayed (idempotently)
    by the first delta. Folder delta first (P2-D-120); root delta only when the folder call is unsupported."""
    drive, folder = channel["files_drive_id"], channel["files_folder_item_id"]
    if not force_root:
        try:
            body = await ctx.graph.get(f"/drives/{drive}/items/{folder}/delta", {"token": "latest"})
            return body["@odata.deltaLink"], "folder"
        except GraphError as e:
            if e.status not in (400, 405, 501):
                raise
            log.warning("folder delta unsupported (status=%s code=%s); falling back to root delta", e.status, e.code)
    body = await ctx.graph.get(f"/drives/{drive}/root/delta", {"token": "latest"})
    return body["@odata.deltaLink"], "root"


def state_key(mode: str, channel_id: str) -> str:
    return f"{mode}:{channel_id}"


# ---------------------------------------------------------------------------- full sync
async def full_sync_channel(ctx: JobContext, channel: dict, force_root: bool = False) -> str:
    """Breadth-first listing of the channel's Files folder. Resumable per folder. Returns "ok" or "partial".
    Documents not seen during a *complete* pass are soft-deleted. Stores the delta link only when complete."""
    cid, drive = channel["id"], channel["files_drive_id"]
    key = f"fullsync:{cid}"
    state = await ctx.storage.get_sync_state(key)
    cur = state["cursor"] if state and state["cursor"] else None
    if cur is None:
        link, mode = await _start_delta_link(ctx, channel, force_root)
        cur = {"started_at": ctx.clock().isoformat(), "queue": [[channel["files_folder_item_id"], ""]],
               "delta_link": link, "mode": mode}
    started = parse_ts(cur["started_at"])
    queue: list[list[str]] = cur["queue"]
    while queue:
        if ctx.budget.expired():
            await ctx.storage.put_sync_state(key, cursor=cur, last_status="partial")
            return "partial"
        folder_id, base = queue[0]
        endpoint = f"/drives/{drive}/items/{folder_id}/children"
        async for it in ctx.graph.items(endpoint):
            raw_id = await store_raw(ctx.storage, "driveItem", endpoint, f"{drive}:{it['id']}", it)
            path = join_path(base, it["name"])
            await apply_item(ctx, channel, it, path, raw_id, ctx.clock())
            if "folder" in it:
                queue.append([it["id"], path])
        queue.pop(0)
    now = ctx.clock()
    for gone in await ctx.storage.soft_delete_unseen(cid, started, now):
        await ctx.storage.add_document_event({"document_id": gone["id"], "event": "deleted", "at": now,
                                              "old_name": gone["name"], "old_path": gone["path"]})
        ctx.stats["deleted"] += 1
    await ctx.storage.delete_sync_state(state_key("folder", cid))
    await ctx.storage.delete_sync_state(state_key("root", cid))
    await ctx.storage.put_sync_state(state_key(cur["mode"], cid), delta_link=cur["delta_link"], last_status="ok")
    await ctx.storage.delete_sync_state(key)
    await ctx.storage.update_channel(cid, last_synced_at=now, sync_error=None)
    ctx.stats["full_syncs"] += 1
    return "ok"

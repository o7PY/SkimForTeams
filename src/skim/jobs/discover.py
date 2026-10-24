"""FR-202: channels + Files folders for every installed team. Standard channels only (P2-D-124)."""
from __future__ import annotations

import logging

from ..graph.auth import GraphAuthError
from ..graph.client import GraphError
from .common import JobContext, store_raw

log = logging.getLogger("skim.jobs")


async def discover_team(ctx: JobContext, team: dict) -> None:
    """Never raises for Graph problems: they are recorded (channels.sync_error / stats) so one team or
    channel cannot abort the run. Private/shared channels are counted and otherwise ignored."""
    gid, st = team["aad_group_id"], ctx.stats
    path = f"/teams/{gid}/channels"
    try:
        channels = [c async for c in ctx.graph.items(path)]
    except (GraphError, GraphAuthError) as e:
        st["team_errors"] += 1
        log.warning("discover: cannot list channels status=%s code=%s", e.status, e.code)
        return
    for ch in channels:
        if ch.get("membershipType") != "standard":
            st["skipped_non_standard"] += 1  # nothing else: no row, no names, no further calls
            continue
        await store_raw(ctx.storage, "channel", path, f"{gid}:{ch['id']}", ch)
        await ctx.storage.upsert_channel({"id": ch["id"], "team_id": team["id"], "display_name": ch.get("displayName"),
                                          "membership_type": "standard"})
        st["channels"] += 1
        await _resolve_files_folder(ctx, gid, ch["id"])
    await ctx.storage.update_team(team["id"], last_discovered_at=ctx.clock())


async def _resolve_files_folder(ctx: JobContext, gid: str, channel_id: str) -> None:
    path = f"/teams/{gid}/channels/{channel_id}/filesFolder"
    try:
        ff = await ctx.graph.get(path)
        drive_id, item_id = ff["parentReference"]["driveId"], ff["id"]
    except (GraphError, GraphAuthError, KeyError) as e:
        code = getattr(e, "code", None) or type(e).__name__
        status = getattr(e, "status", None)
        await ctx.storage.update_channel(channel_id, sync_error=f"filesFolder failed: {status or ''} {code}".strip())
        ctx.stats["channel_errors"] += 1
        return
    await store_raw(ctx.storage, "driveItem", path, f"{drive_id}:{item_id}", ff)
    await ctx.storage.update_channel(channel_id, files_drive_id=drive_id, files_folder_item_id=item_id, sync_error=None)
    ctx.stats["folders_resolved"] += 1

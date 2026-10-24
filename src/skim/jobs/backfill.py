"""FR-205: message history for standard channels via Graph (P2-D-114/115). Resumable, time-boxed, idempotent.

Per channel: page through /messages (newest first), then each message's /replies. Every payload goes to graph_raw
first. Rows are stored with source='graph'; a message already captured live is never overwritten, only its empty
columns are filled. Messages older than the cut-off are skipped, and system events (member added, ...) are not
messages. Cursor in sync_state `backfill:<channelId>`: {cutoff, count, next, pending} -- `next` is the next
top-level page, `pending` the message ids whose replies are still to be fetched.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..graph.auth import GraphAuthError
from ..graph.client import GraphError
from ..normalize import parse_ts
from ..normalize_graph import chat_message_row
from .common import JobContext, store_raw

log = logging.getLogger("skim.jobs")
PAGE = 50


def state_key(channel_id: str) -> str:
    return f"backfill:{channel_id}"


async def _store(ctx: JobContext, channel_id: str, msg: dict, endpoint: str, cutoff) -> bool:
    """Store one message; returns True if it counts towards the per-channel cap."""
    ctx.stats[f"type_{msg.get('messageType') or 'message'}"] += 1  # what Graph really returned (system events!)
    created = parse_ts(msg["createdDateTime"]) if msg.get("createdDateTime") else None
    if created is not None and created < cutoff:
        ctx.stats["messages_before_cutoff"] += 1
        return False
    await store_raw(ctx.storage, "chatMessage", endpoint, f"{channel_id}:{msg.get('id')}", msg)
    row = chat_message_row(msg, channel_id)
    if row is None:
        ctx.stats["system_messages_skipped"] += 1
        return False
    outcome = await ctx.storage.backfill_message(row["message"], row["attachments"], row["reactions"])
    ctx.stats[f"messages_{outcome}"] += 1
    if row["message"]["deleted_at"]:
        ctx.stats["messages_deleted_seen"] += 1
    return True


async def backfill_channel(ctx: JobContext, team: dict, channel: dict, since_days: int, max_messages: int) -> str:
    """Returns "ok" (history complete or capped) or "partial" (budget ran out; call again)."""
    cid, gid = channel["id"], team["aad_group_id"]
    key = state_key(cid)
    base = f"/teams/{gid}/channels/{cid}/messages"
    state = await ctx.storage.get_sync_state(key)
    cur = state["cursor"] if state and state["cursor"] else None
    if cur is None:
        cur = {"cutoff": (ctx.clock() - timedelta(days=since_days)).isoformat(), "count": 0, "next": None,
               "pending": [], "started": False, "capped": False}
    cutoff = parse_ts(cur["cutoff"])
    await ctx.storage.upsert_conversation({"id": cid, "conversation_type": "channel", "team_id": team["id"],
                                           "channel_id": cid, "name": channel.get("display_name")})

    async def save() -> None:
        await ctx.storage.put_sync_state(key, cursor=cur, last_status="partial")

    while True:
        while cur["pending"]:  # replies of the previous page first
            if ctx.budget.expired():
                await save()
                return "partial"
            mid = cur["pending"][0]
            rurl = f"{base}/{mid}/replies"
            async for reply in ctx.graph.items(rurl, {"$top": PAGE}):
                if cur["count"] >= max_messages:
                    cur["capped"] = True
                    break
                if await _store(ctx, cid, reply, rurl, cutoff):
                    cur["count"] += 1
                    ctx.stats["replies_stored"] += 1
            cur["pending"].pop(0)
        if cur["capped"] or cur["count"] >= max_messages and cur["next"]:
            ctx.stats["capped_channels"] += 1
            break
        if cur["started"] and not cur["next"]:
            break  # last page done
        if ctx.budget.expired():
            await save()
            return "partial"
        if cur["next"]:
            page = await ctx.graph.get(cur["next"])
        else:
            page = await ctx.graph.get(base, {"$top": PAGE})
        cur["started"], cur["next"] = True, page.get("@odata.nextLink")
        ctx.stats["pages"] += 1
        values = page.get("value", [])
        ids: list[str] = []
        newest = None
        for msg in values:
            stamps = [parse_ts(msg[f]) for f in ("lastModifiedDateTime", "createdDateTime") if msg.get(f)]
            newest = max([newest, *stamps]) if newest and stamps else (max(stamps) if stamps else newest)
            if cur["count"] >= max_messages:
                cur["capped"] = True
                break
            if await _store(ctx, cid, msg, base, cutoff):
                cur["count"] += 1
                ctx.stats["messages_seen"] += 1
                ids.append(str(msg["id"]))
        cur["pending"] = ids
        if values and newest is not None and newest < cutoff:
            cur["next"] = None  # nothing newer than the cut-off on this or any later page
    await ctx.storage.put_sync_state(key, last_status="ok")  # kept (no cursor) until the whole team is done
    ctx.stats["channels_backfilled"] += 1
    return "ok"


async def backfill_team(ctx: JobContext, team: dict, since_days: int, max_messages: int, error_text) -> str:
    """All standard channels of one team. A failing channel is recorded and does not stop the others; the team's
    `needs_initial_sync` is cleared only once every channel finished (P2-D-128)."""
    all_ok, partial = True, False
    channels = await ctx.storage.list_channels(team["id"])
    for ch in channels:
        if ctx.budget.expired():
            return "partial"
        done = await ctx.storage.get_sync_state(state_key(ch["id"]))
        if done and done["last_status"] == "ok":
            continue  # finished in an earlier call of this same backfill
        try:
            status = await backfill_channel(ctx, team, ch, since_days, max_messages)
        except (GraphError, GraphAuthError) as e:
            await ctx.storage.update_channel(ch["id"], sync_error=f"backfill failed: {error_text(e)}")
            ctx.stats["channel_errors"] += 1
            all_ok = False
            continue
        if status == "partial":
            partial = True
            all_ok = False
    if all_ok:
        for ch in channels:  # so a reinstall (or a forced /jobs/backfill) starts from scratch next time
            await ctx.storage.delete_sync_state(state_key(ch["id"]))
        await ctx.storage.update_team(team["id"], needs_initial_sync=False, last_backfill_at=ctx.clock())
    return "partial" if partial else "ok"

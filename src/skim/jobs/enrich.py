"""Enrich live channel messages from Graph's message delta feed (P2-D-143).

Live Teams events never carry a post's subject and can lack importance, html or the file-reference attachments, but
the Graph copy has them. Per standard channel this reads `/teams/{gid}/channels/{cid}/messages/delta` (top-level
messages and replies changed since the last run) and applies the fill-only rule (P2-D-114/139): empty columns are
filled and missing attachments added; nothing present is overwritten. A message Graph knows and we do not is stored
with source='graph' (same as backfill). The deltaLink is kept in sync_state `msgdelta:<channelId>`; the first run
starts from `lastModifiedDateTime gt now - since_days`. Bounded: at most PAGES_PER_CHANNEL pages per channel per
run (a saved nextLink resumes), and the job time budget.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..graph.auth import GraphAuthError
from ..graph.client import GraphError, GraphGone
from ..normalize_graph import chat_message_row
from .common import JobContext, store_raw

log = logging.getLogger("skim.jobs")
PAGES_PER_CHANNEL = 10
PAGE = 50


def state_key(channel_id: str) -> str:
    return f"msgdelta:{channel_id}"


async def _apply(ctx: JobContext, cid: str, endpoint: str, msgs: list[dict]) -> None:
    for msg in msgs:
        if not isinstance(msg, dict) or "@removed" in msg or not msg.get("id"):
            continue
        ctx.stats["delta_messages_seen"] += 1
        await store_raw(ctx.storage, "chatMessage", endpoint, f"{cid}:{msg['id']}", msg)
        row = chat_message_row(msg, cid)
        if row is None:
            ctx.stats["delta_system_messages_skipped"] += 1
            continue
        outcome = await ctx.storage.backfill_message(row["message"], row["attachments"], row["reactions"])
        ctx.stats[f"enrich_{outcome}"] += 1


async def enrich_channel(ctx: JobContext, team: dict, channel: dict, since_days: int) -> str:
    """Returns "ok" or "partial" (more pages next run)."""
    cid = channel["id"]
    endpoint = f"/teams/{team['aad_group_id']}/channels/{cid}/messages/delta"
    key = state_key(cid)
    state = await ctx.storage.get_sync_state(key)
    link = (state["cursor"] or {}).get("next") if state and state["cursor"] else None
    link = link or (state["delta_link"] if state else None)
    first_params = None
    if link is None:
        since = (ctx.clock() - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        first_params = {"$filter": f"lastModifiedDateTime gt {since}", "$top": PAGE}
    saved_delta = state["delta_link"] if state else None
    for _ in range(PAGES_PER_CHANNEL):
        if ctx.budget.expired():
            return "partial"
        try:
            page = await ctx.graph.get(link or endpoint, first_params if link is None else None)
        except GraphGone:  # delta token expired: start over (fill-only makes the replay harmless)
            await ctx.storage.delete_sync_state(key)
            ctx.stats["msgdelta_resets"] += 1
            return "partial"
        ctx.stats["delta_pages"] += 1
        await _apply(ctx, cid, endpoint, page.get("value", []))
        nxt, done = page.get("@odata.nextLink"), page.get("@odata.deltaLink")
        if done:
            await ctx.storage.put_sync_state(key, delta_link=done, last_status="ok")
            ctx.stats["msgdelta_channels_done"] += 1
            return "ok"
        if not nxt:
            raise GraphError("message delta feed ended without a deltaLink", endpoint=endpoint)
        link, first_params = nxt, None
        await ctx.storage.put_sync_state(key, delta_link=saved_delta, cursor={"next": nxt}, last_status="partial")
    return "partial"


async def enrich_team(ctx: JobContext, team: dict, since_days: int, error_text) -> str:
    partial = False
    for ch in await ctx.storage.list_channels(team["id"]):
        if ctx.budget.expired():
            return "partial"
        try:
            partial = await enrich_channel(ctx, team, ch, since_days) == "partial" or partial
        except (GraphError, GraphAuthError) as e:
            ctx.stats["enrich_errors"] += 1
            log.warning("message enrichment failed for a channel: %s", error_text(e))
    return "partial" if partial else "ok"

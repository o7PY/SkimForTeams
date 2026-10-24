"""Live messages that arrived empty (P2-D-140). A channel post with a file upload can reach the bot with no text and
only an empty text/html attachment. The Graph copy has the typed text and the file reference, so every run-all asks
Graph for those messages and applies the fill-only rule (P2-D-114): empty columns are filled, missing attachments
are added, nothing present is overwritten. Messages that stay empty in Graph too (stickers, ...) cost one request
per run each, capped by LIMIT."""
from __future__ import annotations

from ..graph.client import GraphError, GraphNotFound
from ..graph.auth import GraphAuthError
from ..normalize_graph import chat_message_row
from .common import JobContext, store_raw

LIMIT = 100


async def refill_empty(ctx: JobContext, team: dict) -> str:
    gid = team["aad_group_id"]
    for m in await ctx.storage.list_empty_messages(team["id"], LIMIT):
        if ctx.budget.expired():
            return "partial"
        cid, mid, parent = m["conversation_id"], m["message_id"], m.get("reply_to_id")
        url = f"/teams/{gid}/channels/{cid}/messages/{parent}/replies/{mid}" if parent \
            else f"/teams/{gid}/channels/{cid}/messages/{mid}"
        ctx.stats["empty_messages_checked"] += 1
        try:
            msg = await ctx.graph.get(url)
        except GraphNotFound:
            ctx.stats["empty_messages_not_found"] += 1
            continue
        except (GraphError, GraphAuthError):
            ctx.stats["empty_messages_errors"] += 1
            continue
        await store_raw(ctx.storage, "chatMessage", url, f"{cid}:{mid}", msg)
        row = chat_message_row(msg, cid)
        if row is None:
            continue
        outcome = await ctx.storage.backfill_message(row["message"], row["attachments"], row["reactions"])
        if outcome == "filled":
            ctx.stats["empty_messages_refilled"] += 1
    return "ok"

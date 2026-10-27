"""POST /api/v1/chat (PHASE6A): ask a question about the student's courses. Auth like the rest of /api/v1. Nothing is stored but the
time of each accepted question (the rate limit); the question, the history and the answer exist only in this request."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

from aiohttp import web

from ..ai.providers.router import AllProvidersFailed
from ..storage.base import Storage
from .chat import ChatService
from .routes_items import body, err

log = logging.getLogger("skim.chat")

HOURLY, DAILY = 20, 60
TIMEOUT_SECONDS = 25
MAX_QUESTION, MAX_TURN = 500, 1500


def _history(raw) -> list[dict] | None:
    """The last turns the client sends (at most 6); None when the shape is wrong."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 12:
        return None
    out = []
    for t in raw[-6:]:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant") or not isinstance(t.get("text"), str):
            return None
        out.append({"role": t["role"], "text": t["text"][:MAX_TURN]})
    return out


def add_routes(sub: web.Application, storage: Storage, user_key: web.RequestKey, now: Callable[[], datetime],
               chat: ChatService | None) -> None:
    async def post_chat(request: web.Request) -> web.Response:
        if chat is None:   # CHAT_ENABLED is off (or AI is): the app hides the chat
            return err(503, "the course assistant is not available")
        uid = request[user_key]["id"]
        b = await body(request)
        question = b.get("question") if b else None
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > MAX_QUESTION:
            return err(400, f"question is required (up to {MAX_QUESTION} characters)")
        history = _history(b.get("history"))
        if history is None:
            return err(400, "history must be a list of {role: user|assistant, text}")
        item_id = b.get("item_id")
        if item_id is not None:
            if isinstance(item_id, bool) or not isinstance(item_id, int):
                return err(400, "item_id must be a number")
            # The card must be in one of the student's active teams: another team's card is a 404, like everywhere else.
            if item_id not in await storage.accessible_item_ids(uid, [item_id]):
                return err(404, "not found")
            if (await storage.get_feed_items([item_id]))[item_id]["hidden"]:
                if not await storage.get_item_states(uid, [item_id]) and not await storage.get_stars(uid, [item_id]):
                    return err(404, "not found")
        t = now()
        if await storage.count_chat_uses(uid, t - timedelta(hours=1)) >= HOURLY:
            return web.json_response({"error": "You’ve asked a lot of questions this hour. Please try again in a little while.", "scope": "hour"},
                                     status=429, headers={"Retry-After": "600"})
        if await storage.count_chat_uses(uid, t - timedelta(days=1)) >= DAILY:
            return web.json_response({"error": "You’ve reached today’s limit of questions. Please try again tomorrow.", "scope": "day"},
                                     status=429, headers={"Retry-After": "3600"})
        await storage.record_chat_use(uid, t)
        try:
            result = await asyncio.wait_for(chat.answer(uid, question.strip(), item_id, history, t), TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            log.warning("chat timed out")
            return err(504, "That took too long. Please try again.")
        except AllProvidersFailed:
            return err(502, "The course assistant is busy right now. Please try again in a minute.")
        return web.json_response(result)

    sub.router.add_post("/chat", post_chat)

"""Subscriptions, deck, items, swipe state, history and stars (PHASE4.md section 6.2). Every query is scoped to the
token's user; an item outside the user's active teams is a 404, never a 403, so ids cannot be probed."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from aiohttp import web

from ..eventtime import event_end, is_over
from ..storage.base import Storage
from . import cards, deck, user_settings
from .auth import AuthError  # noqa: F401  (re-exported for type checkers of the package)

JOIN_LIMIT = 10                   # P4-D-106: attempts per user per hour
JOIN_WINDOW = timedelta(hours=1)
MAX_SNOOZE = timedelta(days=31)
DECK_LIMIT_DEFAULT, DECK_LIMIT_MAX = 30, 100
DECK_CANDIDATES = 1500
STAR_TEMP = {"1d": timedelta(days=1), "3d": timedelta(days=3), "7d": timedelta(days=7)}


def err(status: int, message: str, **extra) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


def team_json(s: dict) -> dict:
    return {"id": s["team_id"], "name": s["name"], "joined_at": cards.iso(s["joined_at"]), "muted": s["muted"],
            "ended": s["removed_at"] is not None}


async def body(request: web.Request) -> dict | None:
    try:
        v = await request.json()
    except Exception:
        return None
    return v if isinstance(v, dict) else None


def add_routes(sub: web.Application, storage: Storage, user_key: web.RequestKey, now: Callable[[], datetime],
               staff_ids: frozenset[str]) -> None:
    async def settings_of(uid: str) -> dict:
        u = await storage.get_app_user(uid)
        return user_settings.effective(u["settings"] if u else None)

    async def item_or_404(request: web.Request) -> int | None:
        """The {id} of the path if it names a card in one of the user's active teams, else None (-> 404)."""
        raw = request.match_info["id"]
        if not raw.isdigit() or int(raw) > 2**62:
            return None
        iid, uid = int(raw), request[user_key]["id"]
        if iid not in await storage.accessible_item_ids(uid, [iid]):
            return None
        if (await storage.get_feed_items([iid]))[iid]["hidden"]:  # a deleted message / folded duplicate: only for those who already hold it
            if not await storage.get_item_states(uid, [iid]) and not await storage.get_stars(uid, [iid]):
                return None
        return iid

    async def render(uid: str, ids: list[int], folded: dict[int, list[int]], detail: bool = False) -> list[dict]:
        """Cards for ids (in that order); folded originals are loaded too and nested under their card."""
        s = await settings_of(uid)
        every = list(dict.fromkeys([*ids, *[f for fs in folded.values() for f in fs]]))
        ld = await cards.load(storage, uid, every, detail, s, now())
        important = set(s["important_types"])
        out = [cards.build_card(ld, i, now=now(), staff_ids=staff_ids, important=important, folded_ids=folded.get(i), detail=detail)
               for i in ids]
        return [c for c in out if c is not None]

    async def current_deck(uid: str) -> tuple[list[dict], dict[int, list[int]]]:
        t = now()
        cands = await storage.deck_candidates(uid, t, deck.BACKLOG_DAYS, DECK_CANDIDATES)
        ids = sorted({e for c in cands for e in c["event_ids"]})
        events = await storage.get_events_by_ids(ids) if ids else {}
        return deck.build_deck(cands, events, t)

    # ------------------------------------------------------------ subscriptions
    async def join(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        b = await body(request)
        code = b.get("code") if b else None
        if not isinstance(code, str) or not code.strip() or len(code) > 40:
            return err(400, "code is required")
        t = now()
        if await storage.count_join_attempts(uid, t - JOIN_WINDOW) >= JOIN_LIMIT:
            return err(429, "too many attempts, try again later")
        team = await storage.get_team_by_code(code)
        if team is None:
            await storage.record_join_attempt(uid, t, False)
            return err(404, "no team has that code")
        if team.get("removed_at") is not None:
            await storage.record_join_attempt(uid, t, False)
            return err(410, "the bot was removed from that team")
        added = await storage.add_subscription(uid, team["id"], t)
        await storage.record_join_attempt(uid, t, True)
        mine = next(s for s in await storage.list_subscriptions(uid) if s["team_id"] == team["id"])
        if not added:
            return web.json_response({"error": "already joined", "team": team_json(mine)}, status=409)
        return web.json_response({"team": team_json(mine)}, status=201)

    async def list_subs(request: web.Request) -> web.Response:
        return web.json_response({"teams": [team_json(s) for s in await storage.list_subscriptions(request[user_key]["id"])]})

    async def patch_sub(request: web.Request) -> web.Response:
        uid, tid = request[user_key]["id"], request.match_info["team_id"]
        b = await body(request)
        if not b or set(b) != {"muted"} or not isinstance(b["muted"], bool):
            return err(400, "body must be {\"muted\": true|false}")
        if not await storage.set_subscription_muted(uid, tid, b["muted"]):
            return err(404, "not found")
        mine = next(s for s in await storage.list_subscriptions(uid) if s["team_id"] == tid)
        return web.json_response({"team": team_json(mine)})

    async def leave(request: web.Request) -> web.Response:
        if not await storage.leave_subscription(request[user_key]["id"], request.match_info["team_id"], now()):
            return err(404, "not found")
        return web.Response(status=204)

    # ------------------------------------------------------------ deck, items, history
    async def get_deck(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        q = request.query
        limit = DECK_LIMIT_DEFAULT
        if "limit" in q:
            if not q["limit"].isdigit() or not 1 <= int(q["limit"]) <= DECK_LIMIT_MAX:
                return err(400, f"limit must be 1 to {DECK_LIMIT_MAX}")
            limit = int(q["limit"])
        cursor = None
        if q.get("cursor"):
            cursor = deck.decode_cursor(q["cursor"])
            if cursor is None:
                return err(400, "bad cursor")
        stay, folded = await current_deck(uid)
        chosen, nxt = deck.page(stay, limit, cursor)
        return web.json_response({"cards": await render(uid, [c["id"] for c in chosen], folded), "next_cursor": nxt,
                                  "total": len(stay)})

    async def get_item(request: web.Request) -> web.Response:
        iid = await item_or_404(request)
        if iid is None:
            return err(404, "not found")
        uid = request[user_key]["id"]
        _, folded = await current_deck(uid)
        children = (await storage.folded_children(uid, [iid])).get(iid, [])  # already swiped with it
        out = await render(uid, [iid], {iid: folded.get(iid) or children}, detail=True)
        return web.json_response(out[0])

    async def history(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        s = await settings_of(uid)
        limit = s["history_limit"]
        if "limit" in request.query:
            raw = request.query["limit"]
            if not raw.isdigit() or not 1 <= int(raw) <= 500:
                return err(400, "limit must be 1 to 500")
            limit = int(raw)
        rows = await storage.list_history(uid, limit)
        ids = [r["item_id"] for r in rows]
        kids = await storage.folded_children(uid, ids) if ids else {}
        out = await render(uid, ids, kids)
        meta = {r["item_id"]: r for r in rows}
        for c in out:
            c["swiped"] = {"state": meta[c["id"]]["state"], "at": cards.iso(meta[c["id"]]["acted_at"])}
        return web.json_response({"cards": out})

    # ------------------------------------------------------------ swipe state
    async def set_state(request: web.Request) -> web.Response:
        iid = await item_or_404(request)
        if iid is None:
            return err(404, "not found")
        uid = request[user_key]["id"]
        b = await body(request)
        action = b.get("action") if b else None
        if action not in ("dismiss", "open", "undo", "restore", "snooze"):
            return err(400, "action must be dismiss, open, undo, restore or snooze")
        t = now()
        if action in ("undo", "restore"):
            kids = (await storage.folded_children(uid, [iid])).get(iid, [])
            if action == "undo":
                await storage.clear_item_state(uid, [iid, *kids])
            else:  # back at the top of the deck, whatever its age (it is a snooze that ends now)
                await storage.set_item_state(uid, [iid], None, t, snoozed_until=t)
                if kids:
                    await storage.set_item_state(uid, kids, None, t, snoozed_until=t, folded_into=iid)
            return web.json_response({"ok": True, "item_id": iid, "folded_ids": kids})
        until = None
        if action == "snooze":
            until = _parse_until(b.get("until"))
            if until is None or until <= t or until > t + MAX_SNOOZE:
                return err(400, "until must be a future time with an offset, within 31 days")
        _, folded = await current_deck(uid)
        kids = folded.get(iid, [])  # the originals folded into this card go with it
        state = None if action == "snooze" else ("dismissed" if action == "dismiss" else "opened")
        await storage.set_item_state(uid, [iid], state, t, snoozed_until=until)
        if kids:
            await storage.set_item_state(uid, kids, state, t, snoozed_until=until, folded_into=iid)
        return web.json_response({"ok": True, "item_id": iid, "folded_ids": kids})

    # ------------------------------------------------------------ stars
    async def put_star(request: web.Request) -> web.Response:
        iid = await item_or_404(request)
        if iid is None:
            return err(404, "not found")
        uid = request[user_key]["id"]
        b = await body(request)
        if b is None or set(b) - {"temp"} or b.get("temp") not in (None, "1d", "3d", "7d", "event_over"):
            return err(400, "body must be {\"temp\": null|\"1d\"|\"3d\"|\"7d\"|\"event_over\"}")
        temp, t = b.get("temp"), now()
        expires, until_over = None, False
        if temp in STAR_TEMP:
            expires = t + STAR_TEMP[temp]
        elif temp == "event_over":
            item = (await storage.get_feed_items([iid]))[iid]
            evs = list((await storage.get_events_by_ids(item["event_ids"])).values()) if item["event_ids"] else []
            dated = [e for e in evs if event_end(e) is not None]
            if not dated:
                return err(400, "this card has no event with a date")
            if all(is_over(e, t) for e in dated):
                return err(400, "the event is already over")
            until_over = True
        await storage.put_star(uid, iid, expires, until_over, t)
        return web.json_response({"ok": True, "item_id": iid})

    async def delete_star(request: web.Request) -> web.Response:
        iid = await item_or_404(request)
        if iid is None:
            return err(404, "not found")
        await storage.delete_star(request[user_key]["id"], iid)
        return web.Response(status=204)

    async def starred(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        t = now()
        rows = await storage.list_stars(uid)
        ok = await storage.accessible_item_ids(uid, [r["item_id"] for r in rows]) if rows else set()
        rows = [r for r in rows if r["item_id"] in ok]
        items = await storage.get_feed_items([r["item_id"] for r in rows]) if rows else {}
        ev_ids = sorted({e for it in items.values() for e in it["event_ids"]})
        evs = await storage.get_events_by_ids(ev_ids) if ev_ids else {}
        live = [r for r in rows if cards.star_active(r, [evs[e] for e in items[r["item_id"]]["event_ids"] if e in evs], t)]
        out = await render(uid, [r["item_id"] for r in live], {})
        by_id = {r["item_id"]: r for r in live}
        for c in out:
            c["starred_at"] = cards.iso(by_id[c["id"]]["created_at"])
        return web.json_response({"cards": out})

    r = sub.router
    r.add_post("/subscriptions", join)
    r.add_get("/subscriptions", list_subs)
    r.add_patch("/subscriptions/{team_id}", patch_sub)
    r.add_delete("/subscriptions/{team_id}", leave)
    r.add_get("/deck", get_deck)
    r.add_get("/items/{id}", get_item)
    r.add_post("/items/{id}/state", set_state)
    r.add_put("/items/{id}/star", put_star)
    r.add_delete("/items/{id}/star", delete_star)
    r.add_get("/history", history)
    r.add_get("/starred", starred)


def _parse_until(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None

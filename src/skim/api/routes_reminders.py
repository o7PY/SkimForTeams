"""GET/PUT/DELETE /api/v1/reminders (PHASE4.md section 6.2). The server computes every fire time (skim/reminders.py); the app just
schedules what GET returns as local notifications. Rows hold intent, never a stale time."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Callable

from aiohttp import web

from .. import reminders as engine
from ..eventtime import IST, is_upcoming
from ..storage.base import Storage
from . import cards, user_settings
from .routes_items import body, err

MIN_OFFSET, MAX_ABSOLUTE = -30 * 24 * 60, timedelta(days=400)   # a manual offset is 0..30 days BEFORE the start
KINDS = ("manual", "auto_day_before", "auto_same_day")


def _uuid(raw: str) -> str | None:
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return None


def etag(items: list[dict]) -> str:
    return hashlib.sha1("|".join(f"{i['id']}@{i['fire_at']}" for i in items).encode()).hexdigest()[:16]


def add_routes(sub: web.Application, storage: Storage, user_key: web.RequestKey, now: Callable[[], datetime],
               id_secret: str) -> None:
    async def settings_of(uid: str) -> dict:
        u = await storage.get_app_user(uid)
        return user_settings.effective(u["settings"] if u else None)

    async def user_events(uid: str) -> dict[int, dict]:
        return await storage.list_events_for_user(uid, (now() - timedelta(days=2)).astimezone(IST).date())

    async def titled(uid: str, sched: list[engine.Scheduled], events: dict[int, dict], settings: dict) -> list[dict]:
        """The wire form: fire time, and a notification title/body made of course/event info only (P4-D-137)."""
        bare = sorted({s.item_id for s in sched if s.event_id is None and s.item_id})
        names: dict[int, str] = {}
        if bare:  # a reminder on a card with no event is titled with the card's own subject or first words
            ld = await cards.load(storage, uid, bare, False, settings, now(), id_secret)
            for i in bare:
                c = cards.build_card(ld, i, now=now(), staff_ids=frozenset(), important=set())
                m = (c or {}).get("message") or {}
                d = (c or {}).get("document") or {}
                names[i] = (m.get("subject") or (m.get("text") or "")[:60] or d.get("name") or "Skim reminder").strip() or "Skim reminder"
        out = []
        for s in sched:
            e = events.get(s.event_id) if s.event_id else None
            title, text = engine.describe(e, s.fire_at, names.get(s.item_id, "Skim reminder"))
            out.append({"id": s.id, "kind": s.kind, "item_id": s.item_id, "event_id": s.event_id, "fire_at": cards.iso(s.fire_at),
                        "title": title, "body": text, "virtual": s.virtual, "offset_minutes": s.offset_minutes})
        return out

    async def computed(uid: str, days: int = engine.DEFAULT_DAYS, cap: int = engine.MAX_SCHEDULED):
        settings, events, rows = await settings_of(uid), await user_events(uid), None
        rows = await storage.list_reminders(uid, include_deleted=True)
        newest = await storage.latest_items_for_events(uid, list(events)) if events else {}
        return engine.compute(uid, rows, events, settings, now(), item_for_event=newest.get, days=days, cap=cap, secret=id_secret), events, settings, rows

    async def get_reminders(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        days = engine.DEFAULT_DAYS
        if "days" in request.query:
            raw = request.query["days"]
            if not raw.isdigit() or not 1 <= int(raw) <= 90:
                return err(400, "days must be 1 to 90")
            days = int(raw)
        sched, events, settings, _ = await computed(uid, days)
        items = await titled(uid, sched, events, settings)
        return web.json_response({"reminders": items, "etag": etag(items), "computed_at": cards.iso(now())})

    async def resolve_event(uid: str, b: dict, events: dict[int, dict]) -> tuple[dict | None, int | None, web.Response | None]:
        """The (event, item id) a PUT refers to, or an error response."""
        item_id = b.get("item_id")
        if item_id is not None and (isinstance(item_id, bool) or not isinstance(item_id, int)):
            return None, None, err(400, "item_id must be a number")
        linked: list[int] = []
        if item_id is not None:
            if item_id not in await storage.accessible_item_ids(uid, [item_id]):
                return None, None, err(404, "not found")
            linked = (await storage.get_feed_items([item_id]))[item_id]["event_ids"]
        event_id = b.get("event_id")
        if event_id is not None:
            if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id not in events:
                return None, None, err(404, "no such event")
            if item_id is not None and event_id not in linked:
                return None, None, err(400, "that event is not on this card")
            return events[event_id], item_id, None
        dated = [events[e] for e in linked if e in events and engine.event_day(events[e]) is not None]
        if len(dated) == 1:
            return dated[0], item_id, None
        return None, item_id, (err(400, "event_id is required: this card has several events") if dated else None)

    async def put_reminder(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        rid = _uuid(request.match_info["id"])
        b = await body(request)
        if rid is None or b is None:
            return err(400, "a uuid in the path and a JSON object body are required")
        settings, events, t = await settings_of(uid), await user_events(uid), now()
        e, item_id, bad = await resolve_event(uid, b, events)
        if bad is not None:
            return bad
        if b.get("recommended") is True:
            if set(b) - {"recommended", "item_id", "event_id"} or item_id is None:
                return err(400, "recommended needs item_id (and optionally event_id) only")
            if e is None or not is_upcoming(e, t):
                return err(400, "this card has no upcoming event with a date")
            times = engine.recommended_times(e, settings["reminders"])
            if not any(x and x > t for x in times.values()):
                return err(400, "no reminder time is left before this event")
            pair = [(rid, "recommended_day_before"), (engine.sibling_id(rid, id_secret), "recommended_same_day")]
            for i, kind in pair:
                if await storage.upsert_reminder({"id": i, "user_id": uid, "item_id": item_id, "event_id": e["id"], "kind": kind}, t) is None:
                    return err(404, "not found")
        else:
            kind = b.get("kind")
            if kind not in KINDS or set(b) - {"kind", "item_id", "event_id", "offset_minutes", "fire_at"}:
                return err(400, "kind must be manual, auto_day_before or auto_same_day")
            offset, fire = b.get("offset_minutes"), b.get("fire_at")
            if offset is not None and fire is not None:
                return err(400, "send offset_minutes or fire_at, not both")
            if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or not MIN_OFFSET <= offset <= 0):
                return err(400, "offset_minutes must be a whole number from -43200 to 0 (minutes before the start)")
            if fire is not None:
                fire = _future(fire, t)
                if fire is None:
                    return err(400, "fire_at must be a future time with an offset, within 400 days")
            if kind == "manual":
                if item_id is None or (offset is None) == (fire is None):
                    return err(400, "a manual reminder needs item_id and exactly one of offset_minutes or fire_at")
                if offset is not None and (e is None or engine.anchor(e) is None):
                    return err(400, "an offset needs a card with a dated event")
            else:  # an automatic reminder the user edits: its id is fixed by (user, event, kind)
                if e is None or rid != engine.virtual_id(uid, e["id"], kind, id_secret):
                    return err(400, "automatic reminders are identified by user, event and kind")
            row = {"id": rid, "user_id": uid, "item_id": item_id, "event_id": e["id"] if e else None, "kind": kind,
                   "offset_minutes": offset, "fire_at": fire}
            if await storage.upsert_reminder(row, t) is None:
                return err(404, "not found")
        sched, ev, settings, _ = await computed(uid, days=365, cap=10_000)
        mine = {rid, engine.sibling_id(rid, id_secret)}
        items = await titled(uid, [s for s in sched if s.id in mine], ev, settings)
        shown = {i["id"]: i for i in items}
        if b.get("recommended") is True:  # a dropped / already-past time still gets its row, shown with no time
            for i, kind in pair:
                shown.setdefault(i, {"id": i, "kind": kind, "item_id": item_id, "event_id": e["id"], "fire_at": None,
                                     "title": e["title"], "body": "", "virtual": False, "offset_minutes": None})
            return web.json_response({"reminders": [shown[i] for i, _ in pair]})
        return web.json_response({"reminder": shown.get(rid) or {"id": rid, "kind": kind, "item_id": item_id, "fire_at": None}})

    async def delete_reminder(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        rid = _uuid(request.match_info["id"])
        if rid is None:
            return err(404, "not found")
        t = now()
        row = await storage.get_reminder(uid, rid)
        if row is not None:
            if row["deleted_at"] is None and not await storage.tombstone_reminder(uid, row, t):
                return err(404, "not found")
            return web.Response(status=204)
        events = await user_events(uid)
        hit = engine.virtual_catalog(uid, events, id_secret).get(rid)
        if hit is None:
            return err(404, "not found")
        eid, kind = hit  # an automatic reminder that was never stored: a tombstone keeps it from coming back
        newest = await storage.latest_items_for_events(uid, [eid])
        if not await storage.tombstone_reminder(uid, {"id": rid, "item_id": newest.get(eid), "event_id": eid, "kind": kind}, t):
            return err(404, "not found")  # the id is taken by someone else's row: never answer "deleted" when nothing was
        return web.Response(status=204)

    sub.router.add_get("/reminders", get_reminders)
    sub.router.add_put("/reminders/{id}", put_reminder)
    sub.router.add_delete("/reminders/{id}", delete_reminder)


def _future(v, now: datetime) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None and now < dt <= now + MAX_ABSOLUTE else None

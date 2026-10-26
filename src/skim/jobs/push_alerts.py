"""push-alerts and push-receipts steps (PHASE4.md P4-D-121..124, 137; section 6.4).

push-alerts: (1) turn NEW event changes into per-user alerts in push_outbox, (2) send the ones that are due, in batches of 100.
push-receipts: ask Expo what happened to tickets older than 15 minutes and switch off tokens that came back DeviceNotRegistered.
Never logs a token, a title or a body: stats are numbers only."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from ..api import user_settings
from ..feed import CHANGE_CARD_TYPES
from ..push import BATCH, MAX_AGE, ExpoError, defer_until, message_text, to_messages, wants_alert
from ..storage.base import Storage
from .common import JobContext

log = logging.getLogger("skim.push")
CURSOR_KEY = "push-alerts"
ENQUEUE_LIMIT = 200
SEND_LIMIT = 300
RECEIPT_AGE = timedelta(minutes=15)
RECEIPT_GIVE_UP = timedelta(hours=24)   # Expo keeps receipts about a day
RECEIPT_CHUNK = 300


class PushService:
    """What the jobs need from the outside: the on/off switch (PUSH_ENABLED) and an Expo client (send / receipts)."""

    def __init__(self, client, enabled: bool = True) -> None:
        self.client, self.enabled = client, enabled


def _add(stats, key: str, n: int = 1) -> None:
    if n:
        stats[key] += n


async def enqueue(storage: Storage, now: datetime, stats) -> str:
    state = await storage.get_sync_state(CURSOR_KEY)
    last = ((state or {}).get("cursor") or {}).get("last_change_id")
    if last is None:  # first run: today's history is not news. Remember where we are and alert only about what comes next.
        await storage.put_sync_state(CURSOR_KEY, cursor={"last_change_id": await storage.max_event_change_id()}, last_status="ok")
        _add(stats, "push_cursor_initialised")
        return "ok"
    changes = await storage.list_changes_after(last, CHANGE_CARD_TYPES, ENQUEUE_LIMIT)
    if not changes:
        return "ok"
    events = await storage.get_events_by_ids(sorted({c["event_id"] for c in changes}))
    team_names = {t["id"]: t.get("name") for t in await storage.list_teams()}
    newest: dict[int, dict] = {}
    for c in changes:  # several changes of one event in one pass (e.g. uncancelled + rescheduled): one alert, the latest
        if c["event_id"] in newest:
            _add(stats, "push_changes_coalesced")
        newest[c["event_id"]] = c
    for c in newest.values():
        e = events.get(c["event_id"])
        if e is None or now - c["at"] > MAX_AGE:
            _add(stats, "push_changes_skipped")
            continue
        title, body = message_text(c["change_type"], e, team_names.get(c["team_id"]))
        card = await storage.feed_item_for_change(c["id"])
        for aud in await storage.alert_audience(c["team_id"], e["id"]):
            if not wants_alert(c, e, aud, now):
                _add(stats, "push_not_wanted")
                continue
            item = card or (await storage.latest_items_for_events(aud["user_id"], [e["id"]])).get(e["id"])
            data = {"type": "sync", "event_id": e["id"], **({"item_id": item} if item else {})}
            quiet = user_settings.effective(aud["settings"])["quiet_hours"]
            queued = await storage.add_push({"user_id": aud["user_id"], "event_change_id": c["id"], "title": title, "body": body,
                                             "data": data, "not_before": defer_until(now, quiet)})
            _add(stats, "push_enqueued" if queued else "push_already_queued")
    await storage.put_sync_state(CURSOR_KEY, cursor={"last_change_id": max(c["id"] for c in changes)}, last_status="ok")
    return "partial" if len(changes) >= ENQUEUE_LIMIT else "ok"


def _tickets(row: dict) -> list[list[str]]:
    try:
        v = json.loads(row.get("ticket_id") or "[]")
        return [t for t in v if isinstance(t, list) and len(t) == 2]
    except ValueError:
        return []


async def send_due(storage: Storage, push: PushService, ctx: JobContext, now: datetime) -> str:
    rows = await storage.list_due_push(now, SEND_LIMIT)
    batch: list[tuple[dict, list[str]]] = []   # (outbox row, its device tokens)
    for r in rows:
        tokens = [d["expo_push_token"] for d in await storage.list_devices(r["user_id"], active_only=True)]
        if not tokens:
            await storage.update_push(r["id"], status="skipped", error="no_device")
            _add(ctx.stats, "push_skipped_no_device")
        else:
            batch.append((r, tokens))
    flat = [(r, m) for r, tokens in batch for m in to_messages(tokens, r["title"], r["body"], r["data"] or {})]
    outcome: dict[int, dict] = {r["id"]: {"tickets": [], "error": None} for r, _ in batch}
    for i in range(0, len(flat), BATCH):
        if ctx.budget.expired():
            return "partial"
        chunk = flat[i:i + BATCH]
        try:
            tickets = await push.client.send([m for _, m in chunk])
        except ExpoError as e:
            _add(ctx.stats, "push_transient_errors" if e.transient else "push_rejected_batches")
            for r, _ in chunk:  # transient: stay pending and try again next run; refused: failed for good
                if not e.transient:
                    outcome[r["id"]]["error"] = e.reason
                else:
                    outcome.pop(r["id"], None)
            if e.transient:
                break  # Expo is unhappy right now: stop for this run
            continue
        for (r, m), t in zip(chunk, tickets):
            o = outcome.get(r["id"])
            if o is None:
                continue
            if t.get("status") == "ok" and t.get("id"):
                o["tickets"].append([t["id"], m["to"]])
            else:
                code = (t.get("details") or {}).get("error") or "error"
                o["error"] = o["error"] or code
                if code == "DeviceNotRegistered":
                    await storage.disable_device(m["to"], now)
                    _add(ctx.stats, "push_tokens_disabled")
    for r, _ in batch:
        o = outcome.get(r["id"])
        if o is None:
            continue
        if o["tickets"]:
            await storage.update_push(r["id"], status="sent", ticket_id=json.dumps(o["tickets"]), sent_at=now)
            _add(ctx.stats, "push_sent")
        else:
            await storage.update_push(r["id"], status="failed", error=o["error"] or "error")
            _add(ctx.stats, "push_failed")
    return "partial" if len(rows) >= SEND_LIMIT else "ok"


async def push_alerts(ctx: JobContext, team_id: str | None) -> str:
    push: PushService | None = ctx.options.get("push")
    if push is None or not push.enabled:
        _add(ctx.stats, "push_disabled")
        return "ok"
    now = ctx.clock()
    a = await enqueue(ctx.storage, now, ctx.stats)
    b = await send_due(ctx.storage, push, ctx, now)
    return "partial" if "partial" in (a, b) else "ok"


async def push_receipts(ctx: JobContext, team_id: str | None) -> str:
    push: PushService | None = ctx.options.get("push")
    if push is None or not push.enabled:
        return "ok"
    now = ctx.clock()
    rows = await ctx.storage.list_push_awaiting_receipt(now - RECEIPT_AGE, 200)
    ids = [t[0] for r in rows for t in _tickets(r)]
    got: dict[str, dict] = {}
    try:
        for i in range(0, len(ids), RECEIPT_CHUNK):
            got |= await push.client.receipts(ids[i:i + RECEIPT_CHUNK])
    except ExpoError:
        _add(ctx.stats, "push_receipt_errors")
        return "ok"
    for r in rows:
        tickets = _tickets(r)
        have = [(t, got.get(t[0])) for t in tickets]
        errors = []
        for (tid, token), rec in have:
            if rec and rec.get("status") == "error":
                code = (rec.get("details") or {}).get("error") or "error"
                errors.append(code)
                if code == "DeviceNotRegistered":
                    await ctx.storage.disable_device(token, now)
                    _add(ctx.stats, "push_tokens_disabled")
        if any(rec is None for _, rec in have):
            if r["sent_at"] and now - r["sent_at"] > RECEIPT_GIVE_UP:
                await ctx.storage.update_push(r["id"], receipt_status="expired")
            continue  # Expo has no receipt yet: ask again next run
        await ctx.storage.update_push(r["id"], receipt_status=("error:" + errors[0]) if errors else "ok")
        _add(ctx.stats, "push_receipts_error" if errors else "push_receipts_ok")
    return "ok"

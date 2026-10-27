"""T7: push-alerts and push-receipts against a fake Expo, on both storages."""
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from skim.jobs.common import Budget, JobContext
from skim.jobs.feed_build import build_feed
from skim.jobs.push_alerts import PushService, enqueue, push_alerts, push_receipts
from skim.push import ExpoError
from test_feed_build import CONV, STAFF, analyse, msg, quiz_op, team
from test_reminders_engine import at

NOW = at(2026, 10, 26, 17, 30)               # Mon 26 Oct 17:30 IST: awake hours
TOK = lambda n: f"ExponentPushToken[token{n:04d}xxxxx]"  # noqa: E731


def uid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


class FakeExpo:
    """Records every request. `tickets[token]` = ('error', 'DeviceNotRegistered') makes that token fail; `receipts` is what Expo knows."""

    def __init__(self):
        self.batches: list[list[dict]] = []
        self.bad: dict[str, str] = {}
        self.receipt_map: dict[str, dict] = {}
        self.fail: ExpoError | None = None
        self.receipt_fail: ExpoError | None = None
        self.n = 0

    async def send(self, messages):
        if self.fail:
            raise self.fail
        self.batches.append(messages)
        out = []
        for m in messages:
            self.n += 1
            if m["to"] in self.bad:
                out.append({"status": "error", "message": "x", "details": {"error": self.bad[m["to"]]}})
            else:
                out.append({"status": "ok", "id": f"ticket-{self.n}"})
        return out

    async def receipts(self, ids):
        if self.receipt_fail:
            raise self.receipt_fail
        return {i: self.receipt_map[i] for i in ids if i in self.receipt_map}

    @property
    def sent_to(self):
        return [m["to"] for b in self.batches for m in b]


class Env:
    def __init__(self, store, expo):
        self.store, self.expo, self.now = store, expo, NOW
        self.stats = Counter()

    def ctx(self, enabled=True):
        return JobContext(self.store, None, Budget(60), stats=self.stats, clock=lambda: self.now,
                          options={"push": PushService(self.expo, enabled), "staff_aad_ids": STAFF, "feed_lookback_days": 365})

    async def alerts(self):
        return await push_alerts(self.ctx(), None)

    async def receipts(self):
        return await push_receipts(self.ctx(), None)

    async def user(self, n, settings=None, device=True, muted=False, joined=True, devices=1):
        await self.store.upsert_app_user(uid(n), f"u{n}@x.com", f"U{n}", NOW)
        # These tests are about CHANGE alerts; new-card alerts (tests/test_push_new_cards.py) are switched off here so a test's own
        # posts do not add pushes. A test that wants them says so in `settings`.
        await self.store.set_user_settings(uid(n), {"new_card_alerts": False, **(settings or {})})
        if joined:
            await self.store.add_subscription(uid(n), "T1", NOW - timedelta(days=3))
            if muted:
                await self.store.set_subscription_muted(uid(n), "T1", True)
        if device:
            for d in range(devices):
                await self.store.upsert_device(uid(n), TOK(n * 10 + d), "android", NOW)

    async def event(self, mid="orig", day=date(2026, 11, 2), start=at(2026, 11, 2, 10), **fields):
        s = self.store
        await msg(s, mid, text="hello", at=NOW - timedelta(days=1))
        op = quiz_op()
        op["event"]["event_date"] = day
        res = await s.commit_message_analysis(
            {"conversation_id": CONV, "message_id": mid, "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "event"},
            [op], {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": mid}, "at": NOW - timedelta(days=1)})
        eid = res[0]["event_id"]
        await self.set_event(eid, starts_at=start, time_known=start is not None, venue="LHC 104", **fields)
        return eid

    async def set_event(self, eid, **fields):
        s = self.store
        if getattr(s, "pool", None):
            sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
            await s.pool.execute(f"update events set {sets} where id=$1", eid, *fields.values())
        else:
            s.events[eid].update(fields)

    async def move(self, eid, mid="move", new_day=date(2026, 11, 3), kind="rescheduled", conf="high", at_=None):
        s = self.store
        at_ = at_ or NOW - timedelta(minutes=5)
        await msg(s, mid, text="Quiz moved", at=at_)
        await s.commit_message_analysis(
            {"conversation_id": CONV, "message_id": mid, "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "change"},
            [{"kind": "update", "event_id": eid, "relation": "changed", "fields": {"event_date": new_day, "starts_at": at(new_day.year, new_day.month, new_day.day, 10), "status": "rescheduled"},
              "changes": [{"change_type": kind, "old_values": {"date": "2026-11-02"}, "new_values": {"date": new_day.isoformat()},
                           "confidence": conf}]}],
            {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": mid}, "at": at_})

    async def build(self):
        await build_feed(self.store, STAFF, self.now, 3650, Counter())

    async def outbox(self):
        return {p["user_id"]: p for p in await self.store.list_push()}


@pytest.fixture
async def env(store):
    await team(store)
    return Env(store, FakeExpo())


async def started(env, **kw):
    """An event exists, push-alerts has taken its first (snapshot) run: the next change is news."""
    eid = await env.event(**kw)
    await env.build()
    await env.alerts()
    return eid


# ---------------------------------------------------------------- enqueue
async def test_the_first_run_only_remembers_where_it_is_so_history_never_alerts(env):
    await env.user(1)
    eid = await env.event()
    await env.move(eid)                                               # a change that already exists when push is switched on
    await env.build()
    await env.alerts()
    assert await env.store.list_push() == [] and env.expo.batches == [] and env.stats["push_cursor_initialised"] == 1
    await env.move(eid, mid="move2", new_day=date(2026, 11, 4))      # a NEW one
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1


async def test_who_is_alerted(env):
    eid = await started(env)
    exam = {"important_types": ["exam"]}
    await env.user(1)                                                  # important type ticked (default quiz)       -> yes
    await env.user(2, muted=True)                                      # muted the team                              -> no
    await env.user(3, {**exam, "urgent_only": True})                   # not ticked + urgent-only                    -> no
    await env.user(4, exam)                                            # not ticked, starred the card                -> yes
    await env.user(5, exam)                                            # not ticked, has a reminder                  -> yes
    await env.user(6, exam)                                            # not ticked, nothing                         -> no
    await env.user(7, joined=False)                                    # never joined                                -> no
    await env.user(8)                                                  # important but no device                     -> skipped
    await env.user(9, {**exam, "urgent_only": True})                   # urgent-only but starred                     -> no
    s = env.store
    [card] = [i for i in range(1, 30) if (await s.get_feed_item(i) or {}).get("message_id") == "orig"]
    for n in (4, 9):
        await s.put_star(uid(n), card, None, False, NOW)
    await s.upsert_reminder({"id": "aaaaaaaa-0000-0000-0000-000000000005", "user_id": uid(5), "item_id": card, "event_id": eid,
                             "kind": "manual", "offset_minutes": -30}, NOW)
    await s.delete_device(uid(8), TOK(80))
    # user 11 left the team after joining
    await env.user(11)
    await s.leave_subscription(uid(11), "T1", NOW)
    await env.move(eid)
    await env.build()
    await env.alerts()
    out = await env.outbox()
    assert set(out) == {uid(1), uid(4), uid(5), uid(8)}
    assert env.expo.sent_to == [TOK(10), TOK(40), TOK(50)]
    assert out[uid(8)]["status"] == "skipped" and out[uid(8)]["error"] == "no_device"
    assert all(out[uid(n)]["status"] == "sent" for n in (1, 4, 5))


async def test_the_alert_text_and_data_have_no_names_and_open_the_change_card(env):
    eid = await started(env)
    await env.user(1)
    await env.move(eid)
    await env.build()
    await env.alerts()
    [m] = env.expo.batches[0]
    assert m["title"] == "Quiz 1 moved" and m["body"] == "→ Tue 3 Nov 10:00, LHC 104 · MA 221"
    assert m["data"]["type"] == "sync" and m["data"]["event_id"] == eid
    change_card = None
    for i in range(1, 40):
        if ((await env.store.get_feed_item(i)) or {}).get("kind") == "change":
            change_card = i
    assert m["data"]["item_id"] == change_card
    blob = json.dumps(m)
    assert "hello" not in blob and "Quiz moved" not in blob          # no message text (the posts say "hello" / "Quiz moved")
    assert not re.search(r"\bN\b", blob)                             # and no author name (the fixtures' author is "N")


async def test_quiet_hours_hold_the_push_until_they_end(env):
    eid = await started(env)
    await env.user(1)
    await env.user(2, {"quiet_hours": {"enabled": False}})
    env.now = at(2026, 10, 27, 0, 30)                                 # 00:30 IST
    await env.move(eid, at_=env.now - timedelta(minutes=1))
    await env.build()
    await env.alerts()
    out = await env.outbox()
    assert out[uid(2)]["status"] == "sent"                            # quiet hours off: straight away
    assert out[uid(1)]["status"] == "pending" and out[uid(1)]["not_before"] == at(2026, 10, 27, 7)
    assert env.expo.sent_to == [TOK(20)]
    env.now = at(2026, 10, 27, 6, 59)
    await env.alerts()
    assert env.expo.sent_to == [TOK(20)]
    env.now = at(2026, 10, 27, 7, 1)
    await env.alerts()
    assert env.expo.sent_to == [TOK(20), TOK(10)] and (await env.outbox())[uid(1)]["status"] == "sent"


async def test_nobody_is_alerted_twice_for_the_same_change(env):
    eid = await started(env)
    await env.user(1)
    await env.move(eid)
    await env.build()
    await env.alerts()
    await env.alerts()                                                # the cursor moved on: nothing new
    await env.store.put_sync_state("push-alerts", cursor={"last_change_id": 0})   # even if the cursor were lost...
    await env.alerts()
    assert len(env.expo.sent_to) == 1 and len(await env.store.list_push()) == 1
    assert env.stats["push_already_queued"] >= 1


async def test_several_changes_of_one_event_in_one_pass_are_one_alert(env):
    eid = await started(env)
    await env.user(1)
    await env.move(eid, mid="m1", new_day=date(2026, 11, 3))
    await env.move(eid, mid="m2", new_day=date(2026, 11, 4), at_=NOW - timedelta(minutes=2))
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1 and env.stats["push_changes_coalesced"] == 1


@pytest.mark.parametrize("why,kw,event_fields", [
    ("low-confidence change", {"conf": "low"}, {}),
    ("unconfirmed event", {}, {"needs_review": True}),
    ("event already over", {}, {"starts_at": at(2026, 10, 25, 10), "event_date": date(2026, 10, 25)}),
    ("a change recorded long after it was posted (late backfill)", {"at_": NOW - timedelta(days=5)}, {}),
    ("not an alert type", {"kind": "restated"}, {}),
    ("needs_review change", {"kind": "needs_review"}, {}),
])
async def test_changes_that_must_not_alert(env, why, kw, event_fields):
    eid = await started(env)
    await env.user(1)
    await env.move(eid, **kw)
    if event_fields:
        await env.set_event(eid, **event_fields)                    # the event's state when the change is processed
    await env.build()
    await env.alerts()
    assert env.expo.sent_to == [] and await env.store.list_push() == [], why


async def test_every_alert_type_has_a_title(env):
    eid = await started(env)
    await env.user(1)
    for n, kind in enumerate(["cancelled", "uncancelled", "venue_changed", "time_set", "date_set", "details_changed"]):
        await env.move(eid, mid=f"m{n}", kind=kind)
        await env.build()
        await env.alerts()
    titles = [m["title"] for b in env.expo.batches for m in b]
    assert titles == ["Quiz 1 cancelled", "Quiz 1 is back on", "Quiz 1 new venue", "Quiz 1 time set", "Quiz 1 date set", "Quiz 1 updated"]


async def test_an_ended_team_gets_no_alerts_and_push_off_does_nothing_and_keeps_the_cursor(env):
    eid = await started(env)
    await env.user(1)
    await env.move(eid)
    await env.build()
    assert await push_alerts(env.ctx(enabled=False), None) == "ok" and env.stats["push_disabled"] == 1
    assert (await env.store.get_sync_state("push-alerts"))["cursor"]["last_change_id"] < 99     # untouched: the change is still news
    await env.store.mark_team_removed("T1", NOW)
    await env.alerts()
    assert env.expo.sent_to == []


# ---------------------------------------------------------------- sending
async def test_a_user_with_two_devices_gets_both_and_both_tickets_are_kept(env):
    eid = await started(env)
    await env.user(1, devices=2)
    await env.move(eid)
    await env.build()
    await env.alerts()
    assert env.expo.sent_to == [TOK(10), TOK(11)]
    row = (await env.outbox())[uid(1)]
    assert row["status"] == "sent" and [t[1] for t in json.loads(row["ticket_id"])] == [TOK(10), TOK(11)] and row["sent_at"] == NOW


async def test_a_dead_token_is_disabled_at_once_and_the_alert_still_counts_if_another_device_took_it(env):
    eid = await started(env)
    await env.user(1, devices=2)
    await env.user(2)
    env.expo.bad = {TOK(10): "DeviceNotRegistered", TOK(20): "DeviceNotRegistered"}
    await env.move(eid)
    await env.build()
    await env.alerts()
    out = await env.outbox()
    assert out[uid(1)]["status"] == "sent" and out[uid(2)]["status"] == "failed" and out[uid(2)]["error"] == "DeviceNotRegistered"
    assert [d["expo_push_token"] for d in await env.store.list_devices(uid(1))] == [TOK(11)]
    assert await env.store.list_devices(uid(2)) == []
    assert env.stats["push_tokens_disabled"] == 2


async def test_batches_are_at_most_100_messages(env):
    eid = await started(env)
    for n in range(1, 251):
        await env.user(n)
    await env.move(eid)
    await env.build()
    await env.alerts()
    assert [len(b) for b in env.expo.batches] == [100, 100, 50] and env.stats["push_sent"] == 250
    rows = await env.store.list_push()
    assert len(rows) == 250 and all(r["status"] == "sent" for r in rows)


async def test_a_transient_expo_error_leaves_the_alerts_pending_for_the_next_run(env):
    eid = await started(env)
    await env.user(1)
    env.expo.fail = ExpoError("expo_http_503", True)
    await env.move(eid)
    await env.build()
    assert await env.alerts() == "ok"
    row = (await env.outbox())[uid(1)]
    assert row["status"] == "pending" and env.stats["push_transient_errors"] == 1
    env.expo.fail = None
    await env.alerts()
    assert (await env.outbox())[uid(1)]["status"] == "sent" and env.expo.sent_to == [TOK(10)]


async def test_a_refused_batch_marks_its_alerts_failed(env):
    eid = await started(env)
    await env.user(1)
    env.expo.fail = ExpoError("expo_http_400", False)
    await env.move(eid)
    await env.build()
    await env.alerts()
    row = (await env.outbox())[uid(1)]
    assert row["status"] == "failed" and row["error"] == "expo_http_400"


async def test_the_send_stops_when_the_time_budget_is_spent(env):
    eid = await started(env)
    for n in range(1, 4):
        await env.user(n)
    await env.move(eid)
    await env.build()
    ctx = env.ctx()
    ctx.budget = Budget(-1)                                           # already expired
    assert await push_alerts(ctx, None) == "partial"                  # told to call again
    assert env.expo.sent_to == []                                     # nothing was sent...
    assert {p["status"] for p in await env.store.list_push()} == {"pending"}   # ...and nothing was lost
    await env.alerts()
    assert len(env.expo.sent_to) == 3


# ---------------------------------------------------------------- receipts
async def test_receipts_confirm_wait_and_disable_dead_tokens(env):
    eid = await started(env)
    await env.user(1)
    await env.user(2)
    await env.user(3)
    await env.move(eid)
    await env.build()
    await env.alerts()
    tickets = {n: json.loads((await env.outbox())[uid(n)]["ticket_id"])[0] for n in (1, 2, 3)}
    await env.receipts()                                              # too early: not even asked
    assert all(p["receipt_status"] is None for p in await env.store.list_push())
    env.now += timedelta(minutes=16)
    env.expo.receipt_map = {tickets[1][0]: {"status": "ok"},
                            tickets[2][0]: {"status": "error", "message": "x", "details": {"error": "DeviceNotRegistered"}}}
    await env.receipts()
    out = await env.outbox()
    assert out[uid(1)]["receipt_status"] == "ok" and out[uid(2)]["receipt_status"] == "error:DeviceNotRegistered"
    assert out[uid(3)]["receipt_status"] is None                      # Expo has nothing yet: ask again next run
    assert await env.store.list_devices(uid(2)) == [] and len(await env.store.list_devices(uid(1))) == 1
    env.expo.receipt_map[tickets[3][0]] = {"status": "ok"}
    await env.receipts()
    assert (await env.outbox())[uid(3)]["receipt_status"] == "ok"
    env.stats.clear()
    await env.receipts()                                              # all settled: nothing left to ask
    assert not env.stats


async def test_receipts_that_never_arrive_expire_after_a_day_and_a_receipt_outage_is_harmless(env):
    eid = await started(env)
    await env.user(1)
    await env.move(eid)
    await env.build()
    await env.alerts()
    env.now += timedelta(minutes=20)
    env.expo.receipt_fail = ExpoError("expo_http_503", True)
    assert await env.receipts() == "ok" and env.stats["push_receipt_errors"] == 1
    env.expo.receipt_fail = None
    await env.receipts()
    assert (await env.outbox())[uid(1)]["receipt_status"] is None
    env.now += timedelta(hours=25)
    await env.receipts()
    assert (await env.outbox())[uid(1)]["receipt_status"] == "expired"


async def test_receipts_do_nothing_when_push_is_off(env):
    assert await push_receipts(env.ctx(enabled=False), None) == "ok"


def test_the_push_steps_run_after_feed_build_inside_run_all():
    from skim.jobs.runner import EXTRA_RUN_ALL_STEPS, STEPS, step_feed_build, step_push_alerts, step_push_receipts
    assert STEPS["push-alerts"] is step_push_alerts and STEPS["push-receipts"] is step_push_receipts
    i = EXTRA_RUN_ALL_STEPS.index(step_feed_build)
    assert EXTRA_RUN_ALL_STEPS[i + 1:i + 3] == [step_push_alerts, step_push_receipts]


def test_settings_read_the_push_switches_and_keep_the_token_out_of_repr():
    from skim.config import Settings
    env = {"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory", "PAIRING_SECRET": "s"}
    assert Settings.from_env(env).push_enabled is False
    s = Settings.from_env({**env, "PUSH_ENABLED": "true", "EXPO_ACCESS_TOKEN": "tok-secret-123", "EXPO_PUSH_URL": "http://x/v2/push/"})
    assert s.push_enabled and s.expo_access_token == "tok-secret-123" and s.expo_push_url == "http://x/v2/push"
    assert "tok-secret-123" not in repr(s)

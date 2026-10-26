"""T7: the pure push rules (skim/push.py) and the Expo HTTP client."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from skim import push
from skim.api import user_settings
from test_reminders_engine import at, event

NOW = at(2026, 10, 26, 17, 30)
STARTS = at(2026, 11, 2, 10)


def aud(settings=None, stars=(), has_reminder=False):
    return {"user_id": "u", "settings": settings or {}, "stars": list(stars), "has_reminder": has_reminder}


def change(conf="high", kind="rescheduled"):
    return {"id": 1, "event_id": 1, "change_type": kind, "confidence": conf, "at": NOW}


E = event(start=STARTS)


STAR = {"expires_at": None, "until_event_over": False}
EXAM = {"important_types": ["exam"]}


@pytest.mark.parametrize("why,expect,a,e,c", [
    ("important type ticked (default quiz)", True, aud(), E, change()),
    ("type not ticked, nothing else", False, aud(EXAM), E, change()),
    ("type not ticked but starred", True, aud(EXAM, stars=[STAR]), E, change()),
    ("type not ticked but a reminder", True, aud(EXAM, has_reminder=True), E, change()),
    ("urgent-only: a star does not count", False, aud({**EXAM, "urgent_only": True}, stars=[STAR]), E, change()),
    ("urgent-only: a reminder does not count", False, aud({**EXAM, "urgent_only": True}, has_reminder=True), E, change()),
    ("urgent-only: important type counts", True, aud({"urgent_only": True}), E, change()),
    ("expired temp star does not count", False, aud(EXAM, stars=[{"expires_at": NOW - timedelta(hours=1), "until_event_over": False}]), E, change()),
    ("a live temp star counts", True, aud(EXAM, stars=[{"expires_at": NOW + timedelta(hours=1), "until_event_over": False}]), E, change()),
    ("low-confidence change", False, aud(), E, change(conf="low")),
    ("event needs review", False, aud(), event(start=STARTS, needs_review=True), change()),
    ("event low confidence", False, aud(), event(start=STARTS, confidence="low"), change()),
    ("event already over", False, aud(), event(start=at(2026, 10, 25, 10)), change()),
    ("an undated event is not over", True, aud(), event(), change()),
])
def test_who_gets_an_alert(why, expect, a, e, c):
    assert push.wants_alert(c, e, a, NOW) is expect, why


@pytest.mark.parametrize("now,expect", [
    (at(2026, 10, 26, 22, 59), None),                    # before 23:00: due now
    (at(2026, 10, 26, 23, 0), at(2026, 10, 27, 7)),      # 23:00 sharp is quiet
    (at(2026, 10, 27, 0, 30), at(2026, 10, 27, 7)),      # after midnight: today's 07:00
    (at(2026, 10, 27, 6, 59), at(2026, 10, 27, 7)),
    (at(2026, 10, 27, 7, 0), None),                      # 07:00 sharp is awake again
    (at(2026, 10, 27, 12, 0), None),
])
def test_quiet_hours_default_window(now, expect):
    assert push.defer_until(now, user_settings.DEFAULTS["quiet_hours"]) == (expect or now)


def test_quiet_hours_off_same_start_end_and_a_daytime_window():
    night = at(2026, 10, 27, 1, 0)
    assert push.defer_until(night, {"enabled": False, "start": "23:00", "end": "07:00"}) == night
    assert push.defer_until(night, {"enabled": True, "start": "08:00", "end": "08:00"}) == night
    day = {"enabled": True, "start": "13:00", "end": "15:00"}
    assert push.defer_until(at(2026, 10, 27, 14, 0), day) == at(2026, 10, 27, 15)
    assert push.defer_until(at(2026, 10, 27, 12, 0), day) == at(2026, 10, 27, 12)
    assert push.defer_until(at(2026, 10, 27, 15, 0), day) == at(2026, 10, 27, 15)


def test_message_text_is_course_and_event_info_only():
    e = event(start=STARTS, venue="LHC 104", title="Quiz 1")
    assert push.message_text("rescheduled", e, "MA 221") == ("Quiz 1 moved", "→ Mon 2 Nov 10:00, LHC 104 · MA 221")
    assert push.message_text("cancelled", e, "MA 221") == ("Quiz 1 cancelled", "No longer happening · MA 221")
    assert push.message_text("uncancelled", e, None)[0] == "Quiz 1 is back on"
    assert push.message_text("venue_changed", e, None) == ("Quiz 1 new venue", "→ Mon 2 Nov 10:00, LHC 104")
    assert push.message_text("details_changed", event(), None) == ("Quiz 1 updated", "Open Skim for details")
    from datetime import date
    assert push.message_text("date_set", event(day=date(2026, 11, 2)), "MA 221") == ("Quiz 1 date set", "→ Mon 2 Nov · MA 221")


@pytest.mark.parametrize("token,ok", [("ExponentPushToken[abcdefghijklmnop]", True), ("ExpoPushToken[abc_DEF-123456]", True),
                                      ("ExponentPushToken[short]", False), ("abc", False), ("", False), (None, False), (5, False),
                                      ("ExponentPushToken[abc def ghijk]", False), ("xExponentPushToken[abcdefghijklmnop]", False),
                                      ("ExponentPushToken[abcdefghijklmnop]\n", False)])
def test_token_format(token, ok):
    assert push.valid_token(token) is ok


def test_messages_carry_the_android_channel_and_priority():
    [m] = push.to_messages(["ExponentPushToken[abcdefghijklmnop]"], "t", "b", {"type": "sync", "item_id": 3})
    assert m["channelId"] == "changes" and m["priority"] == "high" and m["data"] == {"type": "sync", "item_id": 3} and m["sound"] == "default"


# ---------------------------------------------------------------- the Expo HTTP client
class FakeExpoServer:
    def __init__(self):
        self.calls, self.status, self.reply = [], 200, None

    async def handle(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.calls.append((request.path, dict(request.headers), payload))
        if self.status != 200:
            return web.Response(status=self.status, text="nope")
        if self.reply is not None:
            return web.json_response(self.reply) if not isinstance(self.reply, str) else web.Response(text=self.reply)
        if request.path.endswith("/send"):
            return web.json_response({"data": [{"status": "ok", "id": f"T{i}"} for i, _ in enumerate(payload)]})
        return web.json_response({"data": {i: {"status": "ok"} for i in payload["ids"]}})


async def expo(token="secret-token"):
    fake = FakeExpoServer()
    app = web.Application()
    app.router.add_post("/v2/push/send", fake.handle)
    app.router.add_post("/v2/push/getReceipts", fake.handle)
    server = TestServer(app)
    await server.start_server()
    client = push.ExpoClient(token, str(server.make_url("/v2/push")))
    return fake, client, server


async def test_send_posts_json_with_the_enhanced_security_bearer_and_parses_tickets():
    fake, client, server = await expo()
    try:
        tickets = await client.send([{"to": "a"}, {"to": "b"}])
        assert tickets == [{"status": "ok", "id": "T0"}, {"status": "ok", "id": "T1"}]
        path, headers, payload = fake.calls[0]
        assert path == "/v2/push/send" and payload == [{"to": "a"}, {"to": "b"}]
        assert headers["Authorization"] == "Bearer secret-token" and headers["Content-Type"] == "application/json"
        assert await client.receipts(["T0"]) == {"T0": {"status": "ok"}}
        assert fake.calls[1][0] == "/v2/push/getReceipts" and fake.calls[1][2] == {"ids": ["T0"]}
    finally:
        await client.close()
        await server.close()


async def test_no_authorization_header_without_a_token():
    fake, client, server = await expo(token=None)
    try:
        await client.send([{"to": "a"}])
        assert "Authorization" not in fake.calls[0][1]
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("status,transient", [(429, True), (500, True), (503, True), (400, False), (401, False), (413, False)])
async def test_http_errors_are_transient_only_when_retrying_can_help(status, transient):
    fake, client, server = await expo()
    fake.status = status
    try:
        with pytest.raises(push.ExpoError) as e:
            await client.send([{"to": "a"}])
        assert e.value.transient is transient and e.value.reason == f"expo_http_{status}"
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("reply", ["not json at all", {"nodata": 1}, {"data": [{"status": "ok", "id": "only-one"}]}])
async def test_malformed_replies_are_transient_errors_not_crashes(reply):
    fake, client, server = await expo()
    fake.reply = reply
    try:
        with pytest.raises(push.ExpoError) as e:
            await client.send([{"to": "a"}, {"to": "b"}])
        assert e.value.transient is True
    finally:
        await client.close()
        await server.close()


async def test_a_dead_network_is_a_transient_error():
    client = push.ExpoClient("t", "http://127.0.0.1:9/v2/push")
    try:
        with pytest.raises(push.ExpoError) as e:
            await client.send([{"to": "a"}])
        assert e.value.transient is True
    finally:
        await client.close()

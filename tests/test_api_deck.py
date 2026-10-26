"""T5: subscriptions, deck (backlog, merge, snooze), items, state, history, stars. Runs on both storages."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from skim.api import deck as deck_logic
from skim.api import routes as api_routes
from skim.api.auth import AuthError
from skim.jobs.feed_build import build_feed
from collections import Counter

from test_feed_build import CONV, NOW, PROF, STAFF, STUDENT, T0, analyse, msg, quiz_op, team

A, B = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001", "bbbbbbbb-bbbb-bbbb-bbbb-000000000002"
USERS = {"a": (A, "Asha"), "b": (B, "Bo")}


class FakeVerifier:
    async def verify(self, token):
        if not token.startswith("u:") or token[2:] not in USERS:
            raise AuthError("bad token")
        uid, name = USERS[token[2:]]
        return {"sub": uid, "email": f"{name.lower()}@x.com", "user_metadata": {"full_name": name}}


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Api:
    def __init__(self, store, client, clock):
        self.store, self.client, self.clock = store, client, clock

    async def call(self, method, path, user="a", **kw):
        r = await self.client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer u:{user}"}, **kw)
        body = None if r.status == 204 else await r.json()
        return r.status, body

    async def join(self, user="a", code="ABC123"):
        return await self.call("POST", "/subscriptions", user, json={"code": code})

    async def deck(self, user="a", **q):
        status, body = await self.call("GET", "/deck", user, params=q)
        assert status == 200, body
        return body

    async def ids(self, user="a"):
        return [c["id"] for c in (await self.deck(user, limit=100))["cards"]]

    async def build(self):
        await build_feed(self.store, STAFF, self.clock(), 3650, Counter())


@pytest.fixture
async def api(store):
    clock = Clock()
    app = web.Application()
    api_routes.register(app, store, FakeVerifier(), now=clock, staff_aad_ids=STAFF)
    client = TestClient(TestServer(app))
    await client.start_server()
    await team(store)
    yield Api(store, client, clock)
    await client.close()


async def card_for(api, mid):
    """The deck/feed item id of a staff message card."""
    await api.build()
    for i in range(1, 60):
        it = await api.store.get_feed_item(i)
        if it and it.get("message_id") == mid:
            return i
    raise AssertionError(f"no card for {mid}")


async def make_event(api, mid, at, day=date(2026, 11, 2), status="scheduled"):
    src = {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": mid}, "at": at}
    op = quiz_op()
    op["event"]["event_date"], op["event"]["status"] = day, status
    res = await api.store.commit_message_analysis(
        {"conversation_id": CONV, "message_id": mid, "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "event"},
        [op], src)
    return res[0]["event_id"]


async def reschedule(api, eid, mid, at, new_day):
    src = {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": mid}, "at": at}
    await api.store.commit_message_analysis(
        {"conversation_id": CONV, "message_id": mid, "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "change"},
        [{"kind": "update", "event_id": eid, "relation": "changed", "fields": {"event_date": new_day, "status": "rescheduled"},
          "changes": [{"change_type": "rescheduled", "old_values": {"date": "2026-11-02", "time": "10:00"},
                       "new_values": {"date": new_day.isoformat(), "time": "10:00"}, "confidence": "high"}]}], src)


# ---------------------------------------------------------------- subscriptions
async def test_join_list_mute_leave_rejoin(api):
    st, b = await api.join(code=" a b c 1 2 3 ")                      # case and spaces do not matter
    assert st == 201 and b["team"]["name"] == "MA 221" and b["team"]["ended"] is False and b["team"]["muted"] is False
    st, b = await api.join()
    assert st == 409 and b["team"]["id"] == "T1"                       # already joined, the team comes back anyway
    assert [t["id"] for t in (await api.call("GET", "/subscriptions"))[1]["teams"]] == ["T1"]
    assert (await api.call("GET", "/subscriptions", "b"))[1]["teams"] == []   # per user
    st, b = await api.call("PATCH", "/subscriptions/T1", json={"muted": True})
    assert st == 200 and b["team"]["muted"] is True
    assert (await api.call("DELETE", "/subscriptions/T1"))[0] == 204
    assert (await api.call("GET", "/subscriptions"))[1]["teams"] == []
    assert (await api.call("DELETE", "/subscriptions/T1"))[0] == 404   # not a member any more
    assert (await api.join())[0] == 201


@pytest.mark.parametrize("body", [{}, {"code": ""}, {"code": 5}, {"code": "x" * 41}, {"nope": 1}, None])
async def test_join_needs_a_code(api, body):
    assert (await api.call("POST", "/subscriptions", json=body))[0] == 400


async def test_wrong_code_404_and_ended_team_410(api):
    assert (await api.join(code="ZZZZZZ"))[0] == 404
    await api.store.mark_team_removed("T1", NOW)
    st, b = await api.join()
    assert st == 410 and "removed" in b["error"]
    assert (await api.call("GET", "/subscriptions"))[1]["teams"] == []


async def test_eleventh_attempt_in_an_hour_is_429_per_user_and_the_window_slides(api):
    for _ in range(10):
        assert (await api.join(code="ZZZZZZ"))[0] == 404
    assert (await api.join(code="ZZZZZZ"))[0] == 429
    assert (await api.join())[0] == 429                                 # even the right code is refused while limited
    assert (await api.join(user="b"))[0] == 201                         # another user is unaffected
    api.clock.advance(minutes=61)
    assert (await api.join())[0] == 201


async def test_mute_and_leave_of_a_team_you_are_not_in_are_404(api):
    assert (await api.call("PATCH", "/subscriptions/T1", json={"muted": True}))[0] == 404
    assert (await api.call("DELETE", "/subscriptions/T1"))[0] == 404
    await api.join()
    for bad in ({}, {"muted": "yes"}, {"muted": True, "x": 1}):
        assert (await api.call("PATCH", "/subscriptions/T1", json=bad))[0] == 400


# ---------------------------------------------------------------- the deck
async def test_a_new_member_gets_the_last_7_days_plus_cards_of_upcoming_events_newest_first(api):
    s = api.store
    await msg(s, "recent", at=NOW - timedelta(days=2))
    await msg(s, "week", at=NOW - timedelta(days=6, hours=20))
    await msg(s, "old", at=NOW - timedelta(days=10))                                 # too old, no event
    await msg(s, "old_upcoming", at=NOW - timedelta(days=30))                        # old, but its event is still ahead
    await msg(s, "old_past", at=NOW - timedelta(days=30, hours=1))
    await make_event(api, "old_upcoming", NOW - timedelta(days=30), date(2026, 11, 2))
    await make_event(api, "old_past", NOW - timedelta(days=30), date(2026, 10, 10))  # over
    await api.build()
    await api.join()
    names = []
    for c in (await api.deck())["cards"]:
        names.append(c["message"]["text"] and c["id"])
    got = {(await s.get_feed_item(i))["message_id"] for i in names}
    assert got == {"recent", "week", "old_upcoming"}
    ordered = [(await s.get_feed_item(i))["message_id"] for i in await api.ids()]
    assert ordered == ["recent", "week", "old_upcoming"]                              # newest first
    c = (await api.deck())["cards"][0]
    assert c["team"] == {"id": "T1", "name": "MA 221", "ended": False}


async def test_cards_made_after_joining_are_dealt_and_cancelled_event_backlog_is_not(api):
    s = api.store
    await api.join()
    assert await api.ids() == []
    await msg(s, "later", at=NOW - timedelta(hours=1))
    await api.build()
    assert len(await api.ids()) == 1
    await msg(s, "cancelled_old", at=NOW - timedelta(days=30))
    await make_event(api, "cancelled_old", NOW - timedelta(days=30), status="cancelled")
    await api.build()
    assert len(await api.ids()) == 1                                                  # a cancelled event is not "upcoming"


async def test_hidden_cards_students_and_other_teams_never_appear(api):
    s = api.store
    await msg(s, "ok")
    await msg(s, "chat", who=STUDENT)
    await analyse(s, "chat", "chitchat")
    await msg(s, "gone")
    await api.build()
    gone = await card_for(api, "gone")
    await s.set_feed_item_hidden(gone, True)
    await s.upsert_team({"id": "T2", "aad_group_id": "g2", "name": "Other", "tenant_id": "x"})
    other = (await s.upsert_feed_item({"kind": "message", "team_id": "T2", "conversation_id": "c2", "message_id": "x",
                                       "occurred_at": NOW - timedelta(hours=1)}, []))[0]
    await api.join()
    ids = await api.ids()
    assert len(ids) == 1 and other not in ids and gone not in ids


async def test_dismiss_leaves_the_deck_and_goes_to_history_with_undo(api):
    s = api.store
    await msg(s, "m1")
    i = await card_for(api, "m1")
    await api.join()
    st, b = await api.call("POST", f"/items/{i}/state", json={"action": "dismiss"})
    assert st == 200 and b == {"ok": True, "item_id": i, "folded_ids": []}
    assert await api.ids() == []
    hist = (await api.call("GET", "/history"))[1]["cards"]
    assert [c["id"] for c in hist] == [i] and hist[0]["swiped"]["state"] == "dismissed"
    assert (await api.call("GET", "/history", "b"))[1]["cards"] == []                 # per user
    await api.call("POST", f"/items/{i}/state", json={"action": "undo"})
    assert await api.ids() == [i] and (await api.call("GET", "/history"))[1]["cards"] == []


async def test_open_marks_opened_and_a_second_user_is_unaffected(api):
    s = api.store
    await msg(s, "m1")
    i = await card_for(api, "m1")
    await api.join()
    await api.join("b")
    await api.call("POST", f"/items/{i}/state", json={"action": "open"})
    assert (await api.call("GET", "/history"))[1]["cards"][0]["swiped"]["state"] == "opened"
    assert await api.ids("b") == [i]


async def test_restore_puts_even_an_old_card_back_at_the_top(api):
    s = api.store
    await msg(s, "old_up", at=NOW - timedelta(days=40))
    await make_event(api, "old_up", NOW - timedelta(days=40), date(2026, 10, 27))
    await msg(s, "new", at=NOW - timedelta(hours=2))
    await api.build()
    old = await card_for(api, "old_up")
    await api.join()
    await api.call("POST", f"/items/{old}/state", json={"action": "dismiss"})
    api.clock.advance(days=3)                                                         # the event is now over: out of the backlog
    assert old not in await api.ids()
    await api.call("POST", f"/items/{old}/state", json={"action": "restore"})
    assert (await api.ids())[0] == old                                                # back, on top, despite its age
    assert (await api.call("GET", "/history"))[1]["cards"] == []


async def test_snooze_hides_the_card_then_returns_it_on_top(api):
    s = api.store
    await msg(s, "old", at=NOW - timedelta(days=3))
    await msg(s, "new", at=NOW - timedelta(hours=1))
    await api.build()
    old = await card_for(api, "old")
    await api.join()
    until = (NOW + timedelta(hours=1)).isoformat()
    st, _ = await api.call("POST", f"/items/{old}/state", json={"action": "snooze", "until": until})
    assert st == 200 and old not in await api.ids()
    c = (await api.call("GET", f"/items/{old}"))[1]
    assert c["user"]["snoozed_until"] is not None
    api.clock.advance(minutes=59)
    assert old not in await api.ids()
    api.clock.advance(minutes=2)
    ids = await api.ids()
    assert ids[0] == old and len(ids) == 2                                            # older card, but back on top
    assert (await api.call("GET", f"/items/{old}"))[1]["user"]["snoozed_until"] is None


@pytest.mark.parametrize("until", [None, "tomorrow", "2026-10-27T10:00:00", (NOW - timedelta(hours=1)).isoformat(),
                                   (NOW + timedelta(days=40)).isoformat(), 5])
async def test_snooze_needs_a_future_time_with_an_offset_within_31_days(api, until):
    await msg(api.store, "m1")
    i = await card_for(api, "m1")
    await api.join()
    assert (await api.call("POST", f"/items/{i}/state", json={"action": "snooze", "until": until}))[0] == 400
    assert await api.ids() == [i]


@pytest.mark.parametrize("body", [{}, {"action": "nuke"}, {"action": 5}, None])
async def test_unknown_actions_are_400(api, body):
    await msg(api.store, "m1")
    i = await card_for(api, "m1")
    await api.join()
    assert (await api.call("POST", f"/items/{i}/state", json=body))[0] == 400


# ---- merge
async def test_a_change_card_folds_the_older_unswiped_cards_of_its_event(api):
    s = api.store
    await msg(s, "orig", at=NOW - timedelta(days=2))
    eid = await make_event(api, "orig", NOW - timedelta(days=2))
    await msg(s, "other", at=NOW - timedelta(days=2, hours=1))                         # unrelated: stays
    await msg(s, "move", at=NOW - timedelta(hours=3), text="Quiz moved to 4 Nov")
    await reschedule(api, eid, "move", NOW - timedelta(hours=3), date(2026, 11, 4))
    await api.build()
    orig, move = await card_for(api, "orig"), await card_for(api, "move")
    await api.join()
    deck = (await api.deck())["cards"]
    kinds = [(c["kind"], c["folded_ids"]) for c in deck]
    change = next(c for c in deck if c["kind"] == "change")
    assert change["change"]["type"] == "rescheduled" and change["change"]["after"]["start"] == "2026-11-04T10:00:00+05:30"
    assert change["change"]["before"]["start"] == "2026-11-02T10:00:00+05:30"
    assert sorted(change["folded_ids"]) == sorted([orig, move]) and len(deck) == 2, kinds   # the message that moved it folds too
    assert {c["id"] for c in deck} == {change["id"], await card_for(api, "other")}
    # swiping the change card swipes every folded original with it
    st, b = await api.call("POST", f"/items/{change['id']}/state", json={"action": "dismiss"})
    assert sorted(b["folded_ids"]) == sorted([orig, move])
    assert await api.ids() == [await card_for(api, "other")]
    hist = (await api.call("GET", "/history"))[1]["cards"]
    assert [c["id"] for c in hist] == [change["id"]] and sorted(hist[0]["folded_ids"]) == sorted([orig, move])
    await api.call("POST", f"/items/{change['id']}/state", json={"action": "undo"})
    deck = (await api.deck())["cards"]
    assert len(deck) == 2 and sorted(next(c for c in deck if c["kind"] == "change")["folded_ids"]) == sorted([orig, move])   # all back, folded again


async def test_already_swiped_originals_are_not_folded_and_newer_cards_stay_separate(api):
    s = api.store
    await msg(s, "orig", at=NOW - timedelta(days=2))
    eid = await make_event(api, "orig", NOW - timedelta(days=2))
    await api.build()
    orig = await card_for(api, "orig")
    await api.join()
    await api.call("POST", f"/items/{orig}/state", json={"action": "dismiss"})         # the user already dealt with it
    await msg(s, "move", at=NOW - timedelta(hours=3))
    await reschedule(api, eid, "move", NOW - timedelta(hours=3), date(2026, 11, 4))
    await msg(s, "after", at=NOW - timedelta(hours=1))                                 # newer than the change, same event
    await s.commit_message_analysis(
        {"conversation_id": CONV, "message_id": "after", "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "event"},
        [{"kind": "update", "event_id": eid, "relation": "restated", "fields": {}, "changes": []}],
        {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": "after"}, "at": NOW - timedelta(hours=1)})
    await api.build()
    deck = (await api.deck())["cards"]
    change = next(c for c in deck if c["kind"] == "change")
    assert orig not in change["folded_ids"]
    assert any(c["kind"] == "message" and c["id"] != change["id"] for c in deck)       # the newer "after" card is its own card


async def test_an_older_change_folds_into_the_newest_change(api):
    s = api.store
    await msg(s, "orig", at=NOW - timedelta(days=3))
    eid = await make_event(api, "orig", NOW - timedelta(days=3))
    await msg(s, "c1", at=NOW - timedelta(days=2), who=STUDENT)                        # a student's message the AI calls a change is a card too
    await reschedule(api, eid, "c1", NOW - timedelta(days=2), date(2026, 11, 3))
    await msg(s, "c2", at=NOW - timedelta(hours=2), who=STUDENT)
    await reschedule(api, eid, "c2", NOW - timedelta(hours=2), date(2026, 11, 4))
    await api.build()
    await api.join()
    deck = (await api.deck())["cards"]
    assert len(deck) == 1 and deck[0]["kind"] == "change" and deck[0]["change"]["after"]["start"].startswith("2026-11-04")
    assert len(deck[0]["folded_ids"]) == 4        # original, c1's message, the earlier change card, c2's message: all older, same event


# ---- paging
async def test_paging_walks_the_whole_deck_once(api):
    s = api.store
    for n in range(7):
        await msg(s, f"m{n}", at=NOW - timedelta(hours=n + 1))
    await api.build()
    await api.join()
    seen, cursor = [], None
    for _ in range(10):
        body = await api.deck(limit=3, **({"cursor": cursor} if cursor else {}))
        seen += [c["id"] for c in body["cards"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 and len(set(seen)) == 7 and seen == await api.ids()
    assert (await api.call("GET", "/deck", params={"cursor": "!!!"}))[0] == 400
    for bad in ("0", "101", "x"):
        assert (await api.call("GET", "/deck", params={"limit": bad}))[0] == 400


def test_cursor_roundtrip_and_order_are_stable_for_equal_times():
    cards = [{"id": i, "kind": "message", "occurred_at": NOW, "returned": False, "snoozed_until": None, "event_ids": []} for i in (1, 2, 3)]
    first, cur = deck_logic.page(cards, 2, None)
    rest, nxt = deck_logic.page(cards, 2, deck_logic.decode_cursor(cur))
    assert [c["id"] for c in first + rest] == [3, 2, 1] and nxt is None
    assert deck_logic.decode_cursor("garbage") is None and deck_logic.decode_cursor("") is None


# ---------------------------------------------------------------- access control
async def test_every_item_route_404s_for_an_item_of_a_team_the_user_is_not_in(api):
    s = api.store
    await msg(s, "m1")
    i = await card_for(api, "m1")
    await api.join("b")                                                                # B is in T1, A is in nothing
    routes = [("GET", f"/items/{i}", None), ("POST", f"/items/{i}/state", {"action": "dismiss"}),
              ("POST", f"/items/{i}/state", {"action": "undo"}), ("POST", f"/items/{i}/state", {"action": "restore"}),
              ("POST", f"/items/{i}/state", {"action": "snooze", "until": (NOW + timedelta(hours=1)).isoformat()}),
              ("PUT", f"/items/{i}/star", {"temp": None}), ("DELETE", f"/items/{i}/star", None)]
    for method, path, body in routes:
        st, _ = await api.call(method, path, "a", **({"json": body} if body is not None else {}))
        assert st == 404, (method, path)
    assert await s.get_item_states(A, [i]) == {} and await s.list_stars(A) == []       # and nothing was written
    for bad in ("999999", "abc", "-1", str(2**70)):
        assert (await api.call("GET", f"/items/{bad}"))[0] == 404


async def test_leaving_a_team_removes_access_state_and_stars(api):
    s = api.store
    await msg(s, "m1")
    i = await card_for(api, "m1")
    await api.join()
    await api.call("PUT", f"/items/{i}/star", json={"temp": None})
    await api.call("POST", f"/items/{i}/state", json={"action": "dismiss"})
    await api.call("DELETE", "/subscriptions/T1")
    assert (await api.call("GET", f"/items/{i}"))[0] == 404
    assert (await api.call("GET", "/history"))[1]["cards"] == [] and (await api.call("GET", "/starred"))[1]["cards"] == []
    assert await s.get_item_states(A, [i]) == {}
    await api.join()                                                                    # joining again starts fresh
    assert await api.ids() == [i]


async def test_an_ended_team_keeps_its_cards_history_and_stars(api):
    s = api.store
    await msg(s, "m1")
    await msg(s, "m2", at=NOW - timedelta(hours=3))
    a, b = await card_for(api, "m1"), await card_for(api, "m2")
    await api.join()
    await api.call("PUT", f"/items/{a}/star", json={"temp": None})
    await api.call("POST", f"/items/{b}/state", json={"action": "dismiss"})
    await s.mark_team_removed("T1", NOW)
    deck = (await api.deck())["cards"]
    assert [c["id"] for c in deck] == [a] and deck[0]["team"]["ended"] is True
    assert [c["id"] for c in (await api.call("GET", "/starred"))[1]["cards"]] == [a]
    assert [c["id"] for c in (await api.call("GET", "/history"))[1]["cards"]] == [b]
    assert (await api.call("GET", "/subscriptions"))[1]["teams"][0]["ended"] is True


# ---------------------------------------------------------------- card content
async def test_message_card_content_preview_detail_edit_reply_and_deep_links(api):
    s = api.store
    await s.upsert_channel({"id": CONV, "team_id": "T1", "display_name": "General", "membership_type": "standard"})
    long = "word " * 120
    await msg(s, "1000", text=long, at=NOW - timedelta(hours=5))
    await msg(s, "2000", text="short reply", reply_to="1000", at=NOW - timedelta(hours=4))
    await api.build()
    await api.join()
    deck = {c["id"]: c for c in (await api.deck())["cards"]}
    root = next(c for c in deck.values() if c["message"]["text"].startswith("word"))
    reply = next(c for c in deck.values() if c["message"]["text"] == "short reply")
    assert root["message"]["text_truncated"] is True and len(root["message"]["text"]) <= 281 and root["message"]["text"].endswith("…")
    full = (await api.call("GET", f"/items/{root['id']}"))[1]
    assert full["message"]["text"] == long and full["message"]["text_truncated"] is False
    assert full["author"] == {"name": "N", "is_staff": True}
    base = f"https://teams.cloud.microsoft/l/message/{CONV}/"
    tail = f"&teamName={quote('MA 221', safe='')}&channelName=General&createdTime="
    assert root["links"]["teams_deep_link"] == f"{base}1000?tenantId=x&groupId=g1&parentMessageId=1000{tail}1000"
    assert reply["links"]["teams_deep_link"] == f"{base}2000?tenantId=x&groupId=g1&parentMessageId=1000{tail}2000"   # reply form
    assert reply["message"]["reply_to_subject"].startswith("word word")


async def test_file_chips_document_cards_and_deleted_messages(api):
    s = api.store
    await msg(s, "post", text="slides attached", atts=[("slides.pdf", "https://x/slides.pdf")])
    d = await s.upsert_document({"drive_id": "dr", "item_id": "slides", "team_id": "T1", "channel_id": CONV, "name": "slides.pdf",
                                 "parent_item_id": "root", "path": "slides.pdf", "created_by_aad": PROF, "created_by_name": "Prof P",
                                 "web_url": "https://sp/slides.pdf"})
    [att] = await s.list_unlinked_attachments("T1", 10)
    await s.add_attachment_link(att["id"], d, "web_url")
    d2 = await s.upsert_document({"drive_id": "dr", "item_id": "notes", "team_id": "T1", "channel_id": CONV, "name": "notes v2.pdf",
                                  "parent_item_id": "root", "path": "notes v2.pdf", "created_by_aad": PROF, "created_by_name": "Prof P",
                                  "web_url": "https://sp/notes.pdf"})
    await s.add_document_event({"document_id": d2, "event": "renamed", "at": NOW - timedelta(hours=2), "old_name": "notes.pdf",
                                "new_name": "notes v2.pdf", "old_path": "notes.pdf", "new_path": "notes v2.pdf"})
    await api.build()
    await api.join()
    deck = (await api.deck())["cards"]
    m = next(c for c in deck if c["kind"] == "message")
    assert m["message"]["attachments"] == [{"name": "slides.pdf", "document_id": d, "web_url": "https://sp/slides.pdf"}]
    f = next(c for c in deck if c["kind"] == "document")
    assert f["document"] == {"name": "notes v2.pdf", "action": "renamed", "old_name": "notes.pdf", "old_path": "notes.pdf",
                             "web_url": "https://sp/notes.pdf"}
    assert f["links"]["file_url"] == "https://sp/notes.pdf" and f["author"] == {"name": "Prof P", "is_staff": True}
    assert len(deck) == 2                                                               # the file posted with the message has no card of its own


async def test_events_chips_important_and_unconfirmed_follow_the_user_and_the_event(api):
    s = api.store
    await msg(s, "m1", at=NOW - timedelta(hours=2))
    eid = await make_event(api, "m1", NOW - timedelta(hours=2))
    await s.pool.execute("update events set needs_review=true where id=$1", eid) if hasattr(s, "pool") and s.pool else s.events[eid].update(needs_review=True)
    await api.build()
    await api.join()
    [c] = (await api.deck())["cards"]
    [e] = c["events"]
    assert e["title"] == "Quiz 1" and e["type"] == "quiz" and e["start"] == "2026-11-02" and e["all_day"] is True
    assert e["important"] is True and e["unconfirmed"] is True and e["status"] == "scheduled"
    await api.call("PATCH", "/me/settings", json={"important_types": ["exam"]})
    [c] = (await api.deck())["cards"]
    assert c["events"][0]["important"] is False
    await api.join("b")                                                                  # B never changed it: still the defaults
    assert (await api.deck("b"))["cards"][0]["events"][0]["important"] is True


# ---------------------------------------------------------------- history
async def test_history_is_newest_swipe_first_and_limited(api):
    s = api.store
    for n in range(4):
        await msg(s, f"m{n}", at=NOW - timedelta(hours=n + 1))
    await api.build()
    ids = [await card_for(api, f"m{n}") for n in range(4)]
    await api.join()
    for k, i in enumerate(ids):
        await api.call("POST", f"/items/{i}/state", json={"action": "dismiss"})
        api.clock.advance(minutes=1)
    assert [c["id"] for c in (await api.call("GET", "/history"))[1]["cards"]] == ids[::-1]
    assert [c["id"] for c in (await api.call("GET", "/history", params={"limit": 2}))[1]["cards"]] == ids[::-1][:2]
    await api.call("PATCH", "/me/settings", json={"history_limit": 10})
    assert len((await api.call("GET", "/history"))[1]["cards"]) == 4
    for bad in ("0", "501", "x"):
        assert (await api.call("GET", "/history", params={"limit": bad}))[0] == 400


async def test_deleted_messages_show_in_history_as_deleted(api):
    s = api.store
    await msg(s, "m1")
    i = await card_for(api, "m1")
    await api.join()
    await api.call("POST", f"/items/{i}/state", json={"action": "dismiss"})
    await s.set_feed_item_hidden(i, True)
    [c] = (await api.call("GET", "/history"))[1]["cards"]
    assert c["message"]["deleted"] is True


# ---------------------------------------------------------------- stars
async def test_stars_permanent_listed_newest_first_and_removable(api):
    s = api.store
    await msg(s, "a", at=NOW - timedelta(hours=3))
    await msg(s, "b", at=NOW - timedelta(hours=2))
    await api.build()
    a, b = await card_for(api, "a"), await card_for(api, "b")
    await api.join()
    await api.call("PUT", f"/items/{a}/star", json={"temp": None})
    api.clock.advance(minutes=1)
    await api.call("PUT", f"/items/{b}/star", json={"temp": None})
    assert [c["id"] for c in (await api.call("GET", "/starred"))[1]["cards"]] == [b, a]
    assert (await api.call("GET", f"/items/{a}"))[1]["user"]["starred"] is True
    assert (await api.call("DELETE", f"/items/{a}/star"))[0] == 204 and (await api.call("DELETE", f"/items/{a}/star"))[0] == 204
    assert [c["id"] for c in (await api.call("GET", "/starred"))[1]["cards"]] == [b]
    assert (await api.call("GET", f"/items/{a}"))[1]["user"]["starred"] is False
    api.clock.advance(days=365)
    assert [c["id"] for c in (await api.call("GET", "/starred"))[1]["cards"]] == [b]      # a star never expires


@pytest.mark.parametrize("temp,days", [("1d", 1), ("3d", 3), ("7d", 7)])
async def test_temp_stars_vanish_at_read_time_when_their_clock_runs_out(api, temp, days):
    await msg(api.store, "m1")
    i = await card_for(api, "m1")
    await api.join()
    assert (await api.call("PUT", f"/items/{i}/star", json={"temp": temp}))[0] == 200
    [c] = (await api.call("GET", "/starred"))[1]["cards"]
    assert c["user"]["starred"] is True and c["user"]["temp_star_expires_at"] is not None
    api.clock.advance(days=days, minutes=-1)
    assert len((await api.call("GET", "/starred"))[1]["cards"]) == 1
    api.clock.advance(minutes=2)
    assert (await api.call("GET", "/starred"))[1]["cards"] == []
    assert (await api.call("GET", f"/items/{i}"))[1]["user"]["starred"] is False


async def test_until_its_over_stars_follow_the_event_even_when_it_moves(api):
    s = api.store
    await msg(s, "orig", at=NOW - timedelta(days=1))
    eid = await make_event(api, "orig", NOW - timedelta(days=1), date(2026, 10, 28))
    await s.pool.execute("update events set starts_at=$2, time_known=true where id=$1", eid, datetime(2026, 10, 28, 4, 30, tzinfo=timezone.utc)) \
        if getattr(s, "pool", None) else s.events[eid].update(starts_at=datetime(2026, 10, 28, 4, 30, tzinfo=timezone.utc), time_known=True)
    await api.build()
    i = await card_for(api, "orig")
    await api.join()
    assert (await api.call("PUT", f"/items/{i}/star", json={"temp": "event_over"}))[0] == 200
    api.clock.advance(days=2)                                                           # 28 Oct 10:00 IST + 1 h = 11:00 IST is 05:30 UTC
    assert (await api.call("GET", "/starred"))[1]["cards"] == []
    api.clock.t = NOW
    assert len((await api.call("GET", "/starred"))[1]["cards"]) == 1                     # (time travel back: it is alive again)


async def test_until_its_over_needs_a_dated_event_that_is_not_already_over(api):
    s = api.store
    await msg(s, "plain")
    await msg(s, "past", at=NOW - timedelta(days=2))
    await make_event(api, "past", NOW - timedelta(days=2), date(2026, 10, 24))
    await api.build()
    plain, past = await card_for(api, "plain"), await card_for(api, "past")
    await api.join()
    for i in (plain, past):
        st, b = await api.call("PUT", f"/items/{i}/star", json={"temp": "event_over"})
        assert st == 400
    for bad in ({"temp": "2d"}, {"temp": 1}, {"x": 1}, None):
        assert (await api.call("PUT", f"/items/{plain}/star", json=bad))[0] == 400
    assert (await api.call("GET", "/starred"))[1]["cards"] == []


async def test_a_star_on_a_card_keeps_working_for_the_card_in_the_deck(api):
    await msg(api.store, "m1")
    i = await card_for(api, "m1")
    await api.join()
    await api.call("PUT", f"/items/{i}/star", json={"temp": "3d"})
    [c] = (await api.deck())["cards"]
    assert c["user"]["starred"] is True


async def test_deleted_messages_serve_no_content_anywhere(api):
    s = api.store
    await msg(s, "1000", text="secret exam answers", at=NOW - timedelta(hours=3), atts=[("a.pdf", "https://x/a.pdf")])
    await msg(s, "2000", text="a reply", reply_to="1000", at=NOW - timedelta(hours=2))
    await api.build()
    root, reply = await card_for(api, "1000"), await card_for(api, "2000")
    await api.join()
    await api.call("PUT", f"/items/{root}/star", json={"temp": None})
    await api.call("POST", f"/items/{root}/state", json={"action": "dismiss"})
    await _delete_message(s, "1000")
    await s.set_feed_item_hidden(root, True)                                           # what feed-build's sync does
    for path in (f"/items/{root}",):
        c = (await api.call("GET", path))[1]
        assert c["message"]["deleted"] is True and c["message"]["text"] == "" and c["message"]["subject"] is None
        assert c["message"]["attachments"] == [] and c["links"]["teams_deep_link"] is None
    for listing in ("/history", "/starred"):
        [c] = (await api.call("GET", listing))[1]["cards"]
        assert c["message"]["deleted"] is True and "secret" not in str(c) and c["message"]["attachments"] == []
    r = (await api.call("GET", f"/items/{reply}"))[1]
    assert r["message"]["reply_to_subject"] is None and "secret" not in str(r)         # not quoted through the reply either


async def test_a_hidden_card_nobody_interacted_with_is_a_404(api):
    s = api.store
    await msg(s, "m1")
    await msg(s, "m2")
    await api.build()
    a, b = await card_for(api, "m1"), await card_for(api, "m2")
    await api.join()
    await api.call("POST", f"/items/{b}/state", json={"action": "dismiss"})
    for i in (a, b):
        await s.set_feed_item_hidden(i, True)
    for method, path, body in (("GET", f"/items/{a}", None), ("POST", f"/items/{a}/state", {"action": "dismiss"}),
                               ("PUT", f"/items/{a}/star", {"temp": None})):
        assert (await api.call(method, path, **({"json": body} if body else {})))[0] == 404, path
    assert (await api.call("GET", f"/items/{b}"))[0] == 200                           # the user holds it (it is in their history)


async def _delete_message(s, mid):
    from skim.normalize import MessageEvent
    from test_feed_build import raw
    await s.apply_message_event(MessageEvent(conversation_id=CONV, message_id=mid, event="deleted", at=NOW), await raw(s, "del-" + mid))

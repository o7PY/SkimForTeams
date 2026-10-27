"""New-card alerts (P5-D-13): a push when a NEW post or file arrives, even with the app closed. On both storages, against a fake Expo."""
from datetime import date, datetime, timedelta, timezone

import pytest

from skim.api import user_settings
from skim.push import new_card_text, wants_new_card
from test_feed_build import PROF, analyse, msg
from test_feed_build import doc as make_doc
from test_feed_build import docev
from test_push_jobs import NOW, Env, FakeExpo, TOK, env, started, uid  # noqa: F401  (env is a fixture)
from test_reminders_engine import at

ON = {"new_card_alerts": True}


async def post(env, mid="p2", text="Please bring your ID card.", minutes_ago=5, category="announcement"):
    """A new staff post that becomes a card; returns its card id."""
    await msg(env.store, mid, text=text, at=env.now - timedelta(minutes=minutes_ago))
    await analyse(env.store, mid, category)
    await env.build()
    for i in range(1, 200):
        it = await env.store.get_feed_item(i)
        if it and it.get("message_id") == mid:
            return i
    raise AssertionError(mid)


def item(kind="message", doc_event=None, when=NOW - timedelta(minutes=5)):
    return {"kind": kind, "doc_event": doc_event, "occurred_at": when}


EVENT = {"id": 1, "type": "quiz", "title": "Quiz 2", "starts_at": at(2026, 11, 2, 10), "event_date": date(2026, 11, 2), "venue": "LHC 104",
         "needs_review": False, "confidence": "high", "status": "scheduled", "time_known": True, "ends_at": None}


# ---------------------------------------------------------------- who gets one (pure)
def test_a_new_post_is_wanted_by_default_and_each_reason_to_stay_quiet():
    aud = {"settings": {}, "joined_at": NOW - timedelta(days=3)}
    assert wants_new_card(item(), [], aud, NOW) is True
    assert user_settings.effective({})["new_card_alerts"] is True                         # on unless the student switches it off
    assert wants_new_card(item(), [], {**aud, "settings": {"new_card_alerts": False}}, NOW) is False
    assert wants_new_card(item(when=NOW - timedelta(hours=49)), [], aud, NOW) is False      # a late backfill is history, not news
    assert wants_new_card(item(when=NOW - timedelta(hours=47)), [], aud, NOW) is True
    assert wants_new_card(item(when=NOW - timedelta(days=4)), [], {**aud, "joined_at": NOW - timedelta(days=5)}, NOW) is False
    assert wants_new_card(item(when=NOW - timedelta(hours=1)), [], {**aud, "joined_at": NOW - timedelta(minutes=10)}, NOW) is False  # before they joined
    assert wants_new_card(item(when=NOW - timedelta(minutes=5)), [], {**aud, "joined_at": NOW - timedelta(minutes=10)}, NOW) is True
    assert wants_new_card(item(), [], {"settings": {}}, NOW) is True                       # no join time known: not a reason to hold back


def test_a_file_only_when_it_is_new_and_urgent_only_needs_an_important_event():
    aud = {"settings": {}, "joined_at": NOW - timedelta(days=3)}
    assert wants_new_card(item("document", "created"), [], aud, NOW) is True
    for ev in ("renamed", "moved", "modified", "deleted", "restored", None):
        assert wants_new_card(item("document", ev), [], aud, NOW) is False
    urgent = {**aud, "settings": {"urgent_only": True}}
    assert wants_new_card(item(), [], urgent, NOW) is False                                 # a plain post: not urgent
    assert wants_new_card(item(), [{**EVENT, "type": "lab"}], urgent, NOW) is False         # lab is not ticked as important by default
    assert wants_new_card(item(), [EVENT], urgent, NOW) is True                             # a quiz is
    assert wants_new_card(item(), [{**EVENT, "type": "lab"}], {**urgent, "settings": {"urgent_only": True, "important_types": ["lab"]}}, NOW) is True


# ---------------------------------------------------------------- what it says (pure)
def test_the_text_is_course_and_event_info_only():
    assert new_card_text(item(), [EVENT], "MA 221") == ("New: Quiz 2", "Mon 2 Nov 10:00, LHC 104 · MA 221")
    assert new_card_text(item(), [{**EVENT, "needs_review": True}], "MA 221") == ("New: Quiz 2", "date to be confirmed · MA 221")
    assert new_card_text(item(), [{**EVENT, "starts_at": None, "venue": None}], None) == ("New: Quiz 2", "Mon 2 Nov")
    assert new_card_text(item(), [{**EVENT, "starts_at": None, "event_date": None, "venue": None}], None)[1] == "date not announced"
    assert new_card_text(item(), [EVENT, {**EVENT, "id": 2, "title": "Quiz 3"}], "MA 221")[1].endswith("(+1 more) · MA 221")
    assert new_card_text(item(), [], "MA 221") == ("New post", "Tap to read · MA 221")
    assert new_card_text(item("document", "created"), [], "MA 221") == ("New file", "A file was added · MA 221")
    assert new_card_text(item(), [], None) == ("New post", "Tap to read")


def test_the_setting_is_a_switch_the_student_can_flip_and_nothing_else():
    assert user_settings.validate_patch({"new_card_alerts": False}) == ({"new_card_alerts": False}, [])
    clean, problems = user_settings.validate_patch({"new_card_alerts": "no"})
    assert clean == {} and problems and "new_card_alerts" in problems[0]


# ---------------------------------------------------------------- the job
async def test_history_never_alerts_and_the_next_new_post_does_once_with_the_card_to_open(env):  # noqa: F811
    await env.user(1, ON)
    await env.event()
    await env.build()
    await env.alerts()                                                                      # first run: only remembers where it is
    assert env.stats["push_card_cursor_initialised"] == 1 and await env.store.list_push() == [] and env.expo.batches == []
    card = await post(env)
    await env.alerts()
    assert env.expo.sent_to == [TOK(10)]
    m = env.expo.batches[0][0]
    assert m["title"] == "New post" and m["body"] == "Tap to read · MA 221"
    assert m["data"] == {"type": "sync", "item_id": card} and m["channelId"] == "changes" and m["priority"] == "high"
    assert env.stats["push_card_enqueued"] == 1 and env.stats["push_sent"] == 1
    await env.alerts()
    await env.alerts()
    assert len(env.expo.sent_to) == 1                                                       # never twice about one card


async def test_a_post_about_a_quiz_says_which_quiz_and_when(env):  # noqa: F811
    await env.user(1, ON)
    await started(env)
    await msg(env.store, "q", text="Quiz 2 will be on Monday.", at=NOW - timedelta(minutes=5))
    eid = await env.event(mid="q2", day=date(2026, 11, 9), start=at(2026, 11, 9, 14))
    await env.build()
    await env.alerts()
    titles = [(m["title"], m["body"]) for b in env.expo.batches for m in b]
    assert ("New: Quiz 1", "Mon 9 Nov 14:00, LHC 104 · MA 221") in titles
    assert all("Quiz 2 will be on Monday" not in t + b for t, b in titles)                  # never the message text


async def test_only_subscribers_who_want_it_get_it_and_a_muted_team_stays_quiet(env):  # noqa: F811
    await env.user(1, ON)
    await env.user(2, ON, muted=True)
    await env.user(3, {"new_card_alerts": False})
    await env.user(4, ON, joined=False)                                                     # never joined the team
    await env.user(5, ON, device=False)                                                     # wants it, has no phone registered
    await started(env)
    await post(env)
    await env.alerts()
    assert env.expo.sent_to == [TOK(10)]
    assert env.stats["push_skipped_no_device"] == 1 and env.stats["push_card_not_wanted"] >= 1


async def test_quiet_hours_hold_the_alert_until_morning(env):  # noqa: F811
    await env.user(1, ON)
    await started(env)
    env.now = at(2026, 10, 26, 23, 30)                                                      # inside the default 23:00-07:00
    await post(env, minutes_ago=5)
    await env.alerts()
    assert env.expo.batches == []
    (row,) = await env.store.list_push()
    assert row["status"] == "pending" and row["not_before"] == at(2026, 10, 27, 7, 0)
    env.now = at(2026, 10, 27, 7, 5)
    await env.alerts()
    assert len(env.expo.sent_to) == 1


async def test_urgent_only_gets_a_push_only_for_posts_about_important_kinds(env):  # noqa: F811
    await env.user(1, {"new_card_alerts": True, "urgent_only": True})
    await started(env)
    await post(env, "plain", category="announcement")                                       # no event: not urgent
    await env.alerts()
    assert env.expo.batches == []
    await env.event(mid="q3", day=date(2026, 11, 9), start=at(2026, 11, 9, 14))             # a quiz is ticked as important
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1 and env.expo.batches[0][0]["title"].startswith("New: Quiz")


async def test_a_post_that_moves_a_date_is_one_alert_not_two(env):  # noqa: F811
    await env.user(1, ON)
    eid = await started(env)
    await env.move(eid)                                                                     # "Quiz moved": a change AND a new post
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1                                                       # the change alert, not a second one
    assert env.expo.batches[0][0]["data"]["event_id"] == eid and env.stats["push_card_covered_by_change"] == 1


async def test_the_backlog_of_a_team_you_just_joined_and_old_backfills_are_not_news(env):  # noqa: F811
    await env.user(1, ON)
    await env.user(2, ON, joined=False)
    await started(env)
    await env.store.add_subscription(uid(2), "T1", NOW - timedelta(minutes=10))             # joined 10 minutes ago
    await post(env, "recent", minutes_ago=5)                                                # posted after they joined
    await post(env, "older", minutes_ago=60)                                                # posted before they joined
    await post(env, "ancient", minutes_ago=60 * 24 * 3)                                     # a backfilled old post
    await env.alerts()
    to_u2 = [m for b in env.expo.batches for m in b if m["to"] == TOK(20)]
    assert len(to_u2) == 1                                                                  # only the one posted after they joined
    to_u1 = [m for b in env.expo.batches for m in b if m["to"] == TOK(10)]
    assert len(to_u1) == 2                                                                  # the veteran gets both recent posts, not the 3-day-old one


async def test_a_new_file_alerts_but_a_renamed_or_modified_one_does_not(env):  # noqa: F811
    await env.user(1, ON)
    await started(env)
    d = await make_doc(env.store, "f1", by=PROF, name="notes.pdf")
    await docev(env.store, d, "created", NOW - timedelta(minutes=5))
    await env.build()
    await env.alerts()
    assert [(m["title"], m["body"]) for b in env.expo.batches for m in b] == [("New file", "A file was added · MA 221")]
    await docev(env.store, d, "renamed", NOW - timedelta(minutes=2))
    await docev(env.store, d, "modified", NOW - timedelta(minutes=1))
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1


async def test_a_later_change_alert_is_unaffected_by_new_card_alerts_being_on_or_off(env):  # noqa: F811
    await env.user(1, {"new_card_alerts": False})
    eid = await started(env)
    await env.move(eid)
    await env.build()
    await env.alerts()
    assert len(env.expo.sent_to) == 1 and env.expo.batches[0][0]["data"]["event_id"] == eid


async def test_the_outbox_keeps_its_integrity_rules(env):  # noqa: F811
    s = env.store
    await env.user(1, ON)
    await started(env)
    card = await post(env)
    await env.alerts()
    row = next(p for p in await s.list_push() if p["data"].get("item_id") == card)
    assert row["event_change_id"] is None and row["feed_item_id"] == card
    again = await s.add_card_push({"user_id": uid(1), "feed_item_id": card, "title": "x", "body": "y", "data": {}, "not_before": NOW})
    assert again is False                                                                   # one alert per user per card
    if getattr(s, "pool", None):
        import asyncpg
        with pytest.raises(asyncpg.CheckViolationError):                                    # exactly one of the two sources
            await s.pool.execute("insert into push_outbox (user_id, title, body, not_before) values ($1, 'x', 'y', now())", uid(1))
        with pytest.raises(asyncpg.CheckViolationError):
            await s.pool.execute("insert into push_outbox (user_id, event_change_id, feed_item_id, title, body, not_before) "
                                 "values ($1, (select max(id) from event_changes), $2, 'x', 'y', now())", uid(1), card)

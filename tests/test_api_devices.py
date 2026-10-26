"""T7: /api/v1/devices and /api/v1/sync, on both storages."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from test_api_deck import A, B, api, card_for  # noqa: F401  (api is a fixture)
from test_feed_build import NOW, msg

T1, T2 = "ExponentPushToken[abcdefghijklmnop]", "ExpoPushToken[qrstuvwxyz012345]"


async def test_register_is_idempotent_and_validates_the_token_and_platform(api):
    for _ in range(2):
        assert (await api.call("POST", "/devices", json={"expo_push_token": T1, "platform": "android"}))[0] == 200
    assert [d["expo_push_token"] for d in await api.store.list_devices(A)] == [T1]
    for body in ({}, {"expo_push_token": T1}, {"platform": "android"}, {"expo_push_token": "nope", "platform": "android"},
                 {"expo_push_token": T1, "platform": "windows"}, {"expo_push_token": 5, "platform": "ios"},
                 {"expo_push_token": T1, "platform": "ios", "extra": 1}, None):
        assert (await api.call("POST", "/devices", json=body))[0] == 400, body
    assert len(await api.store.list_devices(A)) == 1


async def test_a_user_can_hold_at_most_ten_active_devices(api):
    for n in range(10):
        assert (await api.call("POST", "/devices", json={"expo_push_token": f"ExponentPushToken[tok{n:010d}]", "platform": "android"}))[0] == 200
    assert (await api.call("POST", "/devices", json={"expo_push_token": "ExponentPushToken[extra0000001]", "platform": "android"}))[0] == 429
    assert (await api.call("POST", "/devices", json={"expo_push_token": "ExponentPushToken[tok0000000003]", "platform": "android"}))[0] == 200   # known one
    assert len(await api.store.list_devices(A)) == 10


async def test_unregister_removes_only_your_own_token(api):
    await api.call("POST", "/devices", json={"expo_push_token": T1, "platform": "android"})
    await api.call("GET", "/me", "b")
    assert (await api.call("DELETE", "/devices/" + quote(T1, safe=""), "b"))[0] == 404          # B cannot remove A's
    assert len(await api.store.list_devices(A)) == 1
    assert (await api.call("DELETE", "/devices/" + quote(T1, safe="")))[0] == 204
    assert (await api.call("DELETE", "/devices/" + quote(T1, safe="")))[0] == 404
    assert await api.store.list_devices(A) == []


async def test_a_phone_that_signs_in_as_someone_else_takes_the_token_with_it(api):
    await api.call("POST", "/devices", json={"expo_push_token": T2, "platform": "android"})
    await api.call("POST", "/devices", "b", json={"expo_push_token": T2, "platform": "android"})
    assert await api.store.list_devices(A) == [] and len(await api.store.list_devices(B)) == 1


async def set_created(api, hours_ago):
    """Make every card look as old as `hours_ago` (card times are the database clock; the tests control them)."""
    old = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    s = api.store
    if getattr(s, "pool", None):
        await s.pool.execute("update feed_items set created_at=$1", old)
    else:
        for f in s.feed_items.values():
            f["created_at"] = old


async def test_sync_says_whether_the_deck_changed_and_gives_the_reminders_etag(api):
    api.clock.t = datetime.now(timezone.utc)
    await msg(api.store, "m1", at=NOW - timedelta(hours=1))
    await api.build()
    await api.join()
    await set_created(api, 3)
    st, first = await api.call("GET", "/sync")
    assert st == 200 and first["deck_changed"] is True and first["reminders_etag"] and first["server_time"].endswith("+05:30")
    since = first["server_time"]                                                                 # "now": m1 is 3 hours older
    st, again = await api.call("GET", "/sync", params={"since": since})
    assert again["deck_changed"] is False and again["reminders_etag"] == first["reminders_etag"]
    await msg(api.store, "m2", at=NOW - timedelta(minutes=5))
    await api.build()                                                                            # a card created just now
    st, newer = await api.call("GET", "/sync", params={"since": since})
    assert newer["deck_changed"] is True


async def test_sync_tolerates_clock_skew_between_the_app_and_the_database(api):
    api.clock.t = datetime.now(timezone.utc)
    await api.join()
    await msg(api.store, "m1", at=NOW - timedelta(hours=1))
    await api.build()
    since = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()                      # the app's clock runs a minute fast
    assert (await api.call("GET", "/sync", params={"since": since}))[1]["deck_changed"] is True   # the card is not missed


async def test_sync_deck_changed_ignores_other_teams_and_other_users(api):
    await api.join()
    await api.call("GET", "/me", "b")                                                            # B has no teams
    await msg(api.store, "m1")
    await api.build()
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    api.clock.t = datetime.now(timezone.utc)
    assert (await api.call("GET", "/sync", "b", params={"since": old}))[1]["deck_changed"] is False
    assert (await api.call("GET", "/sync", params={"since": old}))[1]["deck_changed"] is True


@pytest.mark.parametrize("since", ["yesterday", "2026-10-26T10:00:00", "2099-01-01T00:00:00+00:00"])
async def test_sync_rejects_a_bad_since(api, since):
    assert (await api.call("GET", "/sync", params={"since": since}))[0] == 400


async def test_the_reminders_etag_in_sync_matches_the_reminders_endpoint(api):
    from test_api_reminders import quiz_card
    await quiz_card(api)
    etag = (await api.call("GET", "/reminders"))[1]["etag"]
    assert (await api.call("GET", "/sync"))[1]["reminders_etag"] == etag


async def test_devices_and_sync_need_a_token(api):
    for method, path in (("POST", "/devices"), ("DELETE", "/devices/x"), ("GET", "/sync")):
        assert (await api.client.request(method, "/api/v1" + path)).status == 401

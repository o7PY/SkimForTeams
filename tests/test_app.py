import pytest
from aiohttp.test_utils import TestClient, TestServer

from skim.app import create_app
from skim.config import ConfigError, Settings
from skim.pairing import pairing_code
from skim.storage.memory import MemoryStorage

ANON = {"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory",
        "PAIRING_SECRET": "s3cret", "DEBUG_TOKEN": "tok"}
TEAM = "19:teamabc123@thread.tacv2"
CONV, MSG = "19:chanGeneral@thread.tacv2", "1700000000001"
AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture
async def env():
    sent: list[str] = []

    async def sender(ctx, text):
        sent.append(text)

    store = MemoryStorage()
    client = TestClient(TestServer(create_app(Settings.from_env(ANON), store, sender)))
    await client.start_server()
    yield client, store, sent
    await client.close()


async def post(client, activity):
    return await client.post("/api/messages", json=activity)


async def test_health(env):
    client, _, _ = env
    r = await client.get("/health")
    assert r.status == 200 and await r.json() == {"ok": True, "db": True}


async def test_post_fixtures_stores_everything(env, load):
    client, store, sent = env
    for name in ["channel_post", "message_edit", "message_edit_2", "message_delete", "reaction_added",
                 "channel_post_with_file", "thread_reply", "group_chat_message", "team_renamed", "unknown_type"]:
        r = await post(client, load(name))
        assert r.status < 300, (name, r.status, await r.text())
    assert len(store.raw) == 10
    assert store.messages[(CONV, MSG)]["current_version"] == 4
    assert len(store.reactions) == 1 and len(store.attachments) == 1
    assert sent == []  # silent in channels and group chats (D-007)


async def test_retry_does_not_duplicate(env, load):
    client, store, _ = env
    await post(client, load("channel_post"))
    r = await post(client, load("channel_post"))
    assert r.status < 300 and len(store.raw) == 1


async def test_install_posts_welcome_once_with_pairing_code(env, load):
    client, store, sent = env
    await post(client, load("install_add"))
    await post(client, load("install_add"))  # Teams retry
    code = pairing_code("s3cret", TEAM)
    assert store.teams[TEAM]["pairing_code"] == code
    assert len(sent) == 1 and f"Pairing code: {code}" in sent[0]
    await post(client, load("install_remove"))
    assert store.teams[TEAM]["removed_at"] is not None and len(sent) == 1


async def test_personal_chat_replies_logged(env, load):
    client, _, sent = env
    await post(client, load("personal_message_with_file"))
    assert sent == ["Logged ✅ (1 attachment(s))"]


async def test_malformed_input(env):
    client, store, _ = env
    assert (await client.post("/api/messages", data="not json")).status == 400
    assert (await client.post("/api/messages", json=[1, 2])).status == 400
    r = await post(client, {"type": "message"})  # no conversation: SDK rejects, but we must not 5xx
    assert r.status < 500 and len(store.raw) == 1


async def test_debug_requires_token(env, load):
    client, _, _ = env
    for path in ["/debug/raw", "/debug/messages", "/debug/teams", f"/debug/messages/{CONV}/{MSG}"]:
        assert (await client.get(path)).status == 403
        assert (await client.get(path, headers={"Authorization": "Bearer wrong"})).status == 403
    assert (await client.get("/health")).status == 200  # health stays open


async def test_debug_endpoints(env, load):
    client, _, _ = env
    for name in ["install_add", "channel_post", "message_edit", "reaction_added", "channel_post_with_file"]:
        await post(client, load(name))
    raw = await (await client.get("/debug/raw?limit=2", headers=AUTH)).json()
    assert len(raw) == 2
    only = await (await client.get("/debug/raw?type=installationUpdate", headers=AUTH)).json()
    assert [r["activity_type"] for r in only] == ["installationUpdate"]
    msgs = await (await client.get(f"/debug/messages?team_id={TEAM}", headers=AUTH)).json()
    by_id = {m["message_id"]: m for m in msgs}
    assert by_id[MSG]["version_count"] == 2 and by_id[MSG]["deleted"] is False
    assert by_id["1700000000009"]["attachment_count"] == 1
    one = await (await client.get(f"/debug/messages/{CONV}/{MSG}", headers=AUTH)).json()
    assert len(one["versions"]) == 2 and len(one["reactions"]) == 1
    assert (await client.get(f"/debug/messages/{CONV}/nope", headers=AUTH)).status == 404
    teams = await (await client.get("/debug/teams", headers=AUTH)).json()
    assert teams[0]["pairing_code"] == pairing_code("s3cret", TEAM)


async def test_debug_disabled_without_token(load):
    env = {k: v for k, v in ANON.items() if k != "DEBUG_TOKEN"}
    client = TestClient(TestServer(create_app(Settings.from_env(env), MemoryStorage())))
    await client.start_server()
    try:
        assert (await client.get("/debug/raw", headers=AUTH)).status == 404
    finally:
        await client.close()


async def test_jwt_required_when_not_anonymous(load):
    env = {"STORAGE": "memory", "PAIRING_SECRET": "x",
           "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "cid",
           "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "csecret",
           "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": "tid"}
    store = MemoryStorage()
    client = TestClient(TestServer(create_app(Settings.from_env(env), store)))
    await client.start_server()
    try:
        assert (await post(client, load("channel_post"))).status == 401
        r = await client.post("/api/messages", json=load("channel_post"), headers={"Authorization": "Bearer bad.token.x"})
        assert r.status == 401 and store.raw == []
    finally:
        await client.close()


def test_config_fails_fast_without_leaking_values():
    with pytest.raises(ConfigError) as e:
        Settings.from_env({"STORAGE": "postgres", "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "LEAKME"})
    msg = str(e.value)
    assert "CLIENTID" in msg and "DATABASE_URL" in msg and "PAIRING_SECRET" in msg and "LEAKME" not in msg

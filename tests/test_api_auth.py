"""T3: bearer-token middleware (JWKS + HS256 fallback), /me, settings, account deletion."""
import json
import time
import uuid

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from skim.api import user_settings
from skim.api.auth import JWKS_TTL, REFETCH_COOLDOWN, AuthError, TokenVerifier
from skim.app import create_app
from skim.config import ConfigError, Settings
from skim.storage.memory import MemoryStorage

URL = "https://abc123.supabase.co"
ISS = URL + "/auth/v1"
SECRET = "legacy-hs256-secret-value-0123456789"
UID = "0b6f2c1e-5a3d-4c7e-9a41-2f1d6b8e7c10"
BASE_ENV = {"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory",
            "PAIRING_SECRET": "s", "API_ENABLED": "true", "SUPABASE_URL": URL, "SUPABASE_JWT_SECRET": SECRET}


class Keys:
    """A signing key + its JWKS document; `rotate` swaps in a new key the way Supabase does."""

    def __init__(self, kid="k1", private=None):
        self.kid = kid
        self.private = private or ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict:
        d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        return {**d, "kid": self.kid, "alg": "ES256", "use": "sig"}

    def token(self, **over) -> str:
        now = int(time.time())
        claims = {"sub": UID, "aud": "authenticated", "iss": ISS, "iat": now, "exp": now + 3600, "email": "asha@example.com",
                  "role": "authenticated", "user_metadata": {"full_name": "Asha K"}, **over}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.private, algorithm="ES256", headers={"kid": self.kid})


class Jwks:
    def __init__(self, *keys):
        self.keys, self.calls, self.fail = list(keys), 0, False

    async def __call__(self, url):
        assert url == ISS + "/.well-known/jwks.json"
        self.calls += 1
        if self.fail:
            raise OSError("down")
        return {"keys": [k.jwk() for k in self.keys]}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def hs256(secret=SECRET, **over):
    now = int(time.time())
    claims = {"sub": UID, "aud": "authenticated", "iss": ISS, "exp": now + 3600, **over}
    return jwt.encode(claims, secret, algorithm="HS256")


def verifier(jwks, clock=None, secret=SECRET):
    return TokenVerifier(URL, secret, fetch_jwks=jwks, clock=clock or Clock())


# ---- the verifier ----

async def test_valid_asymmetric_token_is_accepted_and_the_key_set_is_cached():
    k, jwks = Keys(), None
    jwks = Jwks(k)
    v = verifier(jwks)
    assert (await v.verify(k.token()))["sub"] == UID
    await v.verify(k.token())
    assert jwks.calls == 1


@pytest.mark.parametrize("over,why", [
    ({"exp": int(time.time()) - 3600}, "ExpiredSignatureError"),
    ({"aud": "anon"}, "InvalidAudienceError"),
    ({"iss": "https://evil.supabase.co/auth/v1"}, "InvalidIssuerError"),
    ({"sub": None}, "MissingRequiredClaimError"),
    ({"sub": "not-a-uuid"}, "sub is not a uuid"),
    ({"aud": None}, "MissingRequiredClaimError"),
])
async def test_bad_claims_are_rejected(over, why):
    k = Keys()
    with pytest.raises(AuthError) as e:
        await verifier(Jwks(k)).verify(k.token(**over))
    assert e.value.status == 401 and e.value.reason == why


async def test_a_token_signed_by_another_key_with_a_known_kid_is_rejected():
    k, attacker = Keys("k1"), Keys("k1")
    with pytest.raises(AuthError) as e:
        await verifier(Jwks(k)).verify(attacker.token())
    assert e.value.reason == "InvalidSignatureError"


async def test_unknown_kid_refetches_once_then_cools_down_and_a_rotated_key_works():
    old, new, clock = Keys("old"), Keys("new"), Clock()
    jwks = Jwks(old)
    v = verifier(jwks, clock)
    await v.verify(old.token())
    assert jwks.calls == 1
    with pytest.raises(AuthError):
        await v.verify(new.token())                       # unknown kid: one refetch
    assert jwks.calls == 2
    for _ in range(5):                                    # a flood of unknown kids does not refetch every time
        with pytest.raises(AuthError):
            await v.verify(Keys("junk").token())
    assert jwks.calls == 2
    jwks.keys = [old, new]                                # Supabase rotated
    clock.t += REFETCH_COOLDOWN + 1
    assert (await v.verify(new.token()))["sub"] == UID
    assert jwks.calls == 3


async def test_key_set_is_refetched_after_the_ttl():
    k, clock = Keys(), Clock()
    jwks = Jwks(k)
    v = verifier(jwks, clock)
    await v.verify(k.token())
    clock.t += JWKS_TTL - 1
    await v.verify(k.token())
    assert jwks.calls == 1
    clock.t += 2
    await v.verify(k.token())
    assert jwks.calls == 2


async def test_jwks_outage_uses_cached_keys_but_without_a_cache_is_503():
    k, clock = Keys(), Clock()
    jwks = Jwks(k)
    v = verifier(jwks, clock)
    jwks.fail = True
    with pytest.raises(AuthError) as e:
        await v.verify(k.token())
    assert e.value.status == 503
    jwks.fail = False
    await v.verify(k.token())
    jwks.fail = True
    clock.t += JWKS_TTL + 1
    assert (await v.verify(k.token()))["sub"] == UID      # stale keys beat locking everyone out


async def test_hs256_fallback_only_when_the_token_says_hs256_and_a_secret_exists():
    k = Keys()
    v = verifier(Jwks(k))
    assert (await v.verify(hs256()))["sub"] == UID
    with pytest.raises(AuthError) as e:
        await v.verify(hs256(secret="some-other-secret-value-0123456789"))
    assert e.value.reason == "InvalidSignatureError"
    with pytest.raises(AuthError) as e:
        await verifier(Jwks(k), secret=None).verify(hs256())
    assert "no legacy secret" in e.value.reason


async def test_algorithm_confusion_and_none_are_rejected():
    k = Keys()
    v = verifier(Jwks(k))
    # the classic attack: an HS256 token HMAC-signed with the PUBLIC key as the secret (built by hand: PyJWT refuses to)
    import base64
    import hmac
    from cryptography.hazmat.primitives import serialization
    pem = k.private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")  # noqa: E731
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": k.kid}).encode())
    body = b64(json.dumps({"sub": UID, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 60}).encode())
    forged = (head + b"." + body + b"." + b64(hmac.new(pem, head + b"." + body, "sha256").digest())).decode()
    with pytest.raises(AuthError) as e:
        await v.verify(forged)
    assert e.value.reason == "InvalidSignatureError"                         # it is only ever checked against the legacy secret
    with pytest.raises(AuthError):
        await verifier(Jwks(k), secret=None).verify(forged)
    # an ES256-headed token whose key is RSA
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key())), "kid": "r1"}

    async def rsa_jwks(url):
        return {"keys": [jwk]}
    bad = jwt.encode({"sub": UID, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 60}, k.private,
                     algorithm="ES256", headers={"kid": "r1"})
    with pytest.raises(AuthError) as e:
        await TokenVerifier(URL, None, fetch_jwks=rsa_jwks).verify(bad)
    assert "does not match" in e.value.reason
    none_tok = jwt.encode({"sub": UID, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(AuthError) as e:
        await v.verify(none_tok)
    assert "unsupported alg" in e.value.reason


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "Bearer x"])
async def test_garbage_tokens_are_rejected(token):
    with pytest.raises(AuthError):
        await verifier(Jwks(Keys())).verify(token)


async def test_asymmetric_token_without_kid_is_rejected():
    k = Keys()
    t = jwt.encode({"sub": UID, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 60}, k.private, algorithm="ES256")
    with pytest.raises(AuthError) as e:
        await verifier(Jwks(k)).verify(t)
    assert e.value.reason == "no kid"


# ---- the HTTP API ----

class Api:
    def __init__(self, store, k, client):
        self.store, self.k, self.client = store, k, client

    def h(self, **over):
        return {"Authorization": "Bearer " + self.k.token(**over)}


@pytest.fixture
async def api():
    k, store = Keys(), MemoryStorage()
    v = TokenVerifier(URL, SECRET, fetch_jwks=Jwks(k))
    client = TestClient(TestServer(create_app(Settings.from_env(BASE_ENV), store, verifier=v)))
    await client.start_server()
    yield Api(store, k, client)
    await client.close()


async def test_requests_without_a_valid_token_get_401_and_nothing_is_stored(api):
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer junk"},
                    api.h(exp=int(time.time()) - 3600), api.h(aud="anon")):
        r = await api.client.get("/api/v1/me", headers=headers)
        assert r.status == 401 and (await r.json()) == {"error": "unauthorized"} and r.headers["WWW-Authenticate"] == "Bearer"
    assert api.store.app_users == {}


async def test_health_needs_no_student_token_and_unknown_api_paths_do_not_reveal_which_routes_exist(api):
    assert (await api.client.get("/health")).status == 200
    assert (await api.client.get("/api/v1/nothing-here")).status == 401               # no token: the same answer as any route
    assert (await api.client.get("/api/v1/nothing-here", headers=api.h())).status == 404


async def test_me_creates_the_user_from_the_claims_once_with_default_settings(api):
    r = await api.client.get("/api/v1/me", headers=api.h())
    body = await r.json()
    assert r.status == 200
    assert (body["id"], body["email"], body["name"]) == (UID, "asha@example.com", "Asha K")
    assert body["settings"] == user_settings.DEFAULTS
    u = api.store.app_users[UID]
    assert u["settings"] == {} and "token" not in json.dumps(body).lower()


async def test_name_falls_back_to_user_metadata_name_and_a_missing_email_keeps_the_stored_one(api):
    await api.client.get("/api/v1/me", headers=api.h(user_metadata={"name": "Asha Only"}))
    assert api.store.app_users[UID]["name"] == "Asha Only"
    await api.client.get("/api/v1/me", headers=api.h(email=None, user_metadata={}))
    assert api.store.app_users[UID]["email"] == "asha@example.com"


async def test_hs256_legacy_token_works_on_the_api(api):
    r = await api.client.get("/api/v1/me", headers={"Authorization": "Bearer " + hs256()})
    assert r.status == 200 and (await r.json())["id"] == UID


async def test_patch_settings_validates_merges_and_persists_only_the_changes(api):
    r = await api.client.patch("/api/v1/me/settings", headers=api.h(), json={
        "history_limit": 80, "quiet_hours": {"start": "22:30"}, "important_types": ["lab", "quiz", "quiz"],
        "reminders": {"same_day_hours": 4.5}})
    body = await r.json()
    assert r.status == 200
    s = body["settings"]
    assert s["history_limit"] == 80 and s["quiet_hours"] == {"enabled": True, "start": "22:30", "end": "07:00"}
    assert s["important_types"] == ["quiz", "lab"]                                   # canonical order, de-duplicated
    assert s["reminders"] == {"day_before_time": "20:00", "same_day_hours": 4.5, "earliest": "07:00", "auto_for_important": True}
    assert api.store.app_users[UID]["settings"] == {"history_limit": 80, "quiet_hours": {"start": "22:30"},
                                                    "important_types": ["quiz", "lab"], "reminders": {"same_day_hours": 4.5}}
    r = await api.client.patch("/api/v1/me/settings", headers=api.h(), json={"urgent_only": True, "quiet_hours": {"enabled": False}})
    s = (await r.json())["settings"]
    assert s["urgent_only"] is True and s["quiet_hours"] == {"enabled": False, "start": "22:30", "end": "07:00"}
    assert (await (await api.client.get("/api/v1/me", headers=api.h())).json())["settings"] == s


@pytest.mark.parametrize("patch", [
    {"history_limit": 9}, {"history_limit": 501}, {"history_limit": "50"}, {"history_limit": True}, {"history_limit": 50.5},
    {"quiet_hours": {"start": "25:00"}}, {"quiet_hours": {"start": "7:00"}}, {"quiet_hours": {"enabled": "yes"}},
    {"quiet_hours": "off"}, {"important_types": ["quiz", "party"]}, {"important_types": "quiz"}, {"important_types": [1]},
    {"timezone": "Europe/London"}, {"urgent_only": 1}, {"reminders": {"same_day_hours": 0}}, {"reminders": {"same_day_hours": 25}},
    {"reminders": {"same_day_hours": 1.3}}, {"reminders": {"earliest": "noon"}}, {"nonsense": 1}, {"reminders": {"nope": 1}},
    [], "x", None])
async def test_invalid_settings_are_rejected_and_change_nothing(api, patch):
    r = await api.client.patch("/api/v1/me/settings", headers=api.h(), json=patch)
    body = await r.json()
    assert r.status == 400 and body["error"] == "invalid settings" and body["details"]
    assert api.store.app_users[UID]["settings"] == {}


async def test_one_bad_value_blocks_the_whole_patch(api):
    r = await api.client.patch("/api/v1/me/settings", headers=api.h(), json={"history_limit": 100, "urgent_only": "yes"})
    assert r.status == 400 and api.store.app_users[UID]["settings"] == {}


async def test_a_body_that_is_not_json_is_a_400(api):
    r = await api.client.patch("/api/v1/me/settings", headers=api.h(), data="{nope")
    assert r.status == 400


async def test_delete_removes_every_row_and_the_auth_user_and_the_old_token_cannot_bring_it_back(api):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    other = "99999999-9999-9999-9999-999999999999"
    await api.client.get("/api/v1/me", headers=api.h())
    await api.store.upsert_app_user(other, "o@x.com", "Other", now)
    await api.store.upsert_team({"id": "T1", "aad_group_id": "g", "name": "MA", "tenant_id": "x"})
    for u in (UID, other):
        await api.store.add_subscription(u, "T1", now)
        await api.store.upsert_device(u, "tok-" + u[:2], "android", now)
        await api.store.record_join_attempt(u, now, True)
    r = await api.client.delete("/api/v1/me", headers=api.h())
    assert r.status == 204
    assert UID not in api.store.app_users and UID in api.store.auth_deleted
    assert await api.store.list_subscriptions(UID, include_left=True) == [] and await api.store.list_devices(UID, active_only=False) == []
    assert [s["user_id"] for s in await api.store.list_subscriptions(other)] == [other]   # nobody else is touched
    # the access token is still valid for up to an hour, but the account is gone: no resurrection
    api.client.server.app  # noqa: B018 (the middleware's per-instance cache was cleared on delete)
    r = await api.client.get("/api/v1/me", headers=api.h())
    assert r.status == 401 and UID not in api.store.app_users


async def test_delete_twice_is_still_204(api):
    await api.client.get("/api/v1/me", headers=api.h())
    assert (await api.client.delete("/api/v1/me", headers=api.h())).status == 204


async def test_a_cached_user_deleted_elsewhere_gets_401_on_me(api):
    await api.client.get("/api/v1/me", headers=api.h())
    await api.store.delete_app_user(UID)                   # another instance deleted it; this instance still has it cached
    assert (await api.client.get("/api/v1/me", headers=api.h())).status == 401


# ---- config / wiring ----

def test_api_is_off_by_default_and_needs_a_supabase_url_when_on():
    base = {k: v for k, v in BASE_ENV.items() if k not in ("API_ENABLED", "SUPABASE_URL", "SUPABASE_JWT_SECRET")}
    assert Settings.from_env(base).api_enabled is False
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        Settings.from_env({**base, "API_ENABLED": "true"})
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        Settings.from_env({**base, "API_ENABLED": "true", "SUPABASE_URL": "http://insecure"})
    s = Settings.from_env({**base, "API_ENABLED": "true", "SUPABASE_URL": URL + "/"})
    assert s.supabase_url == URL and s.supabase_jwt_secret is None
    assert "legacy" not in repr(Settings.from_env(BASE_ENV)) and SECRET not in repr(Settings.from_env(BASE_ENV))


async def test_the_api_routes_are_absent_when_disabled():
    env = {k: v for k, v in BASE_ENV.items() if k != "API_ENABLED"}
    client = TestClient(TestServer(create_app(Settings.from_env(env), MemoryStorage())))
    await client.start_server()
    try:
        assert (await client.get("/api/v1/me")).status == 404
    finally:
        await client.close()


async def test_tokens_never_reach_the_logs(api, caplog):
    import logging
    token = api.k.token(aud="anon")
    with caplog.at_level(logging.DEBUG):
        await api.client.get("/api/v1/me", headers={"Authorization": "Bearer " + token})
    assert token not in caplog.text and token.split(".")[1] not in caplog.text
    assert "InvalidAudienceError" in caplog.text


# ---- Postgres: the auth.users hooks (S5) ----

async def test_postgres_auth_user_hooks_fall_back_when_there_is_no_auth_schema_and_delete_when_there_is(pg_store):
    s = pg_store
    assert await s.auth_user_exists(UID) is True and await s.delete_auth_user(UID) is False   # no auth schema (local/test db)
    await s.pool.execute("create schema if not exists auth")
    await s.pool.execute("create table auth.users (id uuid primary key)")
    try:
        assert await s.auth_user_exists(UID) is False
        await s.pool.execute("insert into auth.users (id) values ($1)", UID)
        assert await s.auth_user_exists(UID) is True
        assert await s.delete_auth_user(UID) is True and await s.delete_auth_user(UID) is False
        assert await s.auth_user_exists(UID) is False
    finally:
        await s.pool.execute("drop schema auth cascade")

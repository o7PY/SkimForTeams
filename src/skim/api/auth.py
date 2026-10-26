"""Supabase access-token verification (P4-D-104). Asymmetric keys come from the project's JWKS; HS256 with the legacy
secret is accepted ONLY when the token itself says HS256 and a secret is configured. Tokens are never logged."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

import aiohttp
import jwt

log = logging.getLogger("skim.api.auth")

ASYMMETRIC = {"ES256", "RS256", "EdDSA"}
JWKS_TTL = 600.0       # P4 spec 6.1: cached for 10 minutes
REFETCH_COOLDOWN = 30.0  # an unknown kid refetches once, but not on every bad token (no refetch storms)
FAILURE_BACKOFF = 30.0   # after a failed fetch nobody retries for this long (requests fail fast or use the cached keys)
MAX_STALE = 24 * 3600.0  # cached keys older than this are not trusted when Supabase cannot be reached: fail closed (503)
LEEWAY = 10


class AuthError(Exception):
    """`reason` is for logs only (never the token); the client just sees 401, or 503 when the key set is unreachable."""

    def __init__(self, reason: str, status: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


async def _http_jwks(url: str) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
        async with s.get(url) as r:
            r.raise_for_status()
            return await r.json()


class TokenVerifier:
    def __init__(self, supabase_url: str, jwt_secret: str | None = None,
                 fetch_jwks: Callable[[str], Awaitable[dict]] = _http_jwks, clock: Callable[[], float] = time.monotonic) -> None:
        self.issuer = supabase_url.rstrip("/") + "/auth/v1"
        self.jwks_url = self.issuer + "/.well-known/jwks.json"
        self._secret = jwt_secret
        self._fetch = fetch_jwks
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._forced_at: float | None = None
        self._retry_after: float = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        now = self._clock()
        if now < self._retry_after:  # a fetch just failed: do not hit the network again yet
            self._check_usable(now, "jwks unavailable (backing off)")
            return
        try:
            doc = await self._fetch(self.jwks_url)
        except Exception as e:  # network, HTTP status, bad JSON
            self._retry_after = now + FAILURE_BACKOFF
            log.warning("JWKS refresh failed (%s); backing off %ds", type(e).__name__, FAILURE_BACKOFF)
            self._check_usable(now, f"jwks unavailable: {type(e).__name__}")
            return
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in doc.get("keys", []) if isinstance(doc, dict) else []:
            try:
                if jwk.get("kid"):
                    keys[jwk["kid"]] = jwt.PyJWK(jwk)
            except Exception:  # an unusable key must not break the others
                log.warning("ignoring an unusable JWKS key")
        if not keys:  # an empty/garbled key set must never replace good keys (it would lock every student out)
            self._retry_after = now + FAILURE_BACKOFF
            log.warning("JWKS had no usable keys; backing off %ds", FAILURE_BACKOFF)
            self._check_usable(now, "jwks had no usable keys")
            return
        self._keys, self._fetched_at = keys, self._clock()

    def _check_usable(self, now: float, reason: str) -> None:
        """After a failed (or skipped) refresh: cached keys keep working for at most MAX_STALE, else 503 (fail closed)."""
        if not self._keys or self._fetched_at is None or now - self._fetched_at >= MAX_STALE:
            raise AuthError(reason, status=503)

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            now = self._clock()
            refreshed = False
            if self._fetched_at is None or now - self._fetched_at >= JWKS_TTL:
                await self._refresh()
                refreshed = True
            if kid not in self._keys and not refreshed and (self._forced_at is None or now - self._forced_at >= REFETCH_COOLDOWN):
                self._forced_at = now
                await self._refresh()
            key = self._keys.get(kid)
        if key is None:
            raise AuthError("unknown kid")
        return key

    async def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("malformed token") from e
        alg = header.get("alg")
        if alg == "HS256":
            if not self._secret:
                raise AuthError("HS256 token but no legacy secret configured")
            key: object = self._secret
        elif alg in ASYMMETRIC:
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise AuthError("no kid")
            jwk = await self._key_for(kid)
            if jwk.algorithm_name != alg:  # the key is for another algorithm (e.g. an RSA key offered for an ES256 token)
                raise AuthError("alg does not match the key")
            key = jwk.key
        else:
            raise AuthError(f"unsupported alg {str(alg)[:10]!r}")
        try:
            claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated", issuer=self.issuer, leeway=LEEWAY,
                                options={"require": ["exp", "sub", "aud", "iss"]})
        except jwt.PyJWTError as e:
            raise AuthError(type(e).__name__) from e
        try:
            uuid.UUID(str(claims["sub"]))
        except ValueError as e:
            raise AuthError("sub is not a uuid") from e
        return claims

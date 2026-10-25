"""A thin asyncpg pool/connection wrapper that cleans every query parameter (skim.textclean) before it reaches Postgres.
One place covers all 100+ queries, including the Phase 1/2 message writes, so no write path can forget to sanitise."""
from __future__ import annotations

from ..textclean import clean_arg

_METHODS = ("execute", "fetch", "fetchval", "fetchrow")


class _Clean:
    def __init__(self, target) -> None:
        self._t = target

    def __getattr__(self, name):  # transaction(), close(), release(), ... pass straight through
        return getattr(self._t, name)

    async def execute(self, query, *args, **kw):
        return await self._t.execute(query, *map(clean_arg, args), **kw)

    async def fetch(self, query, *args, **kw):
        return await self._t.fetch(query, *map(clean_arg, args), **kw)

    async def fetchval(self, query, *args, **kw):
        return await self._t.fetchval(query, *map(clean_arg, args), **kw)

    async def fetchrow(self, query, *args, **kw):
        return await self._t.fetchrow(query, *map(clean_arg, args), **kw)

    async def executemany(self, query, args, **kw):
        return await self._t.executemany(query, [tuple(map(clean_arg, row)) for row in args], **kw)


class CleanConnection(_Clean):
    pass


class _Acquire:
    def __init__(self, ctx) -> None:
        self._ctx = ctx

    async def __aenter__(self):
        return CleanConnection(await self._ctx.__aenter__())

    async def __aexit__(self, *exc):
        return await self._ctx.__aexit__(*exc)


class CleanPool(_Clean):
    def acquire(self, *a, **kw):
        return _Acquire(self._t.acquire(*a, **kw))

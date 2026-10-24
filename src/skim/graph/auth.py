"""App-only (client-credentials) tokens for Microsoft Graph via MSAL (P2-D-102/103)."""
from __future__ import annotations

import asyncio
from typing import Protocol

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphAuthError(Exception):
    """401/403 from Graph, or a failed token request. `code` is Graph's / AAD's error code, never a secret."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status, self.code, self.endpoint = status, code, endpoint


class TokenProvider(Protocol):
    async def get_token(self) -> str: ...


class MsalTokenProvider:
    """MSAL keeps an in-memory cache and only calls AAD when the cached token is (nearly) expired."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str, authority_host: str = "https://login.microsoftonline.com"):
        import msal

        self._app = msal.ConfidentialClientApplication(
            client_id, client_credential=client_secret, authority=f"{authority_host}/{tenant_id}")

    async def get_token(self) -> str:
        result = await asyncio.to_thread(self._app.acquire_token_for_client, [GRAPH_SCOPE])
        token = result.get("access_token")
        if not token:
            # error / error_description from AAD never contain the secret; the description is cut short anyway.
            raise GraphAuthError(f"token request failed: {result.get('error')} {(result.get('error_description') or '')[:160]}",
                                 code=result.get("error"))
        return token


class StaticTokenProvider:
    """For tests."""

    def __init__(self, token: str = "test-token"):
        self.token, self.calls = token, 0

    async def get_token(self) -> str:
        self.calls += 1
        return self.token

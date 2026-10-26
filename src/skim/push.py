"""Change alerts (PHASE4.md P4-D-121..124, 137): who gets one, what it says, when it may be sent, and the Expo Push client.
The decisions are pure functions so they can be table-tested; the job in jobs/push_alerts.py only moves data around."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

import aiohttp

from .api import user_settings
from .api.cards import star_active
from .eventtime import IST, is_over
from .reminders import anchor, unconfirmed

log = logging.getLogger("skim.push")

TOKEN_RE = re.compile(r"(?:Exponent|Expo)PushToken\[[A-Za-z0-9_-]{8,}\]")
MAX_AGE = timedelta(hours=48)   # a change recorded long after it was posted (a late backfill) is history, not news
BATCH = 100                     # Expo accepts at most 100 messages per request
VERBS = {"rescheduled": "moved", "cancelled": "cancelled", "uncancelled": "is back on", "venue_changed": "new venue",
         "time_set": "time set", "date_set": "date set", "details_changed": "updated"}


def valid_token(token) -> bool:
    return isinstance(token, str) and bool(TOKEN_RE.fullmatch(token))


def wants_alert(change: dict, event: dict, aud: dict, now: datetime) -> bool:
    """P4-D-122 for one subscriber (the audience query already removed muted and departed members).
    Never for an unconfirmed change/event, never for an event that is over. The event must be starred, have a reminder, or be a
    ticked important type; in urgent-only mode only the important-type condition counts."""
    if change.get("confidence") == "low" or unconfirmed(event) or is_over(event, now):
        return False
    s = user_settings.effective(aud.get("settings"))
    if event["type"] in s["important_types"]:
        return True
    if s["urgent_only"]:
        return False
    return aud.get("has_reminder", False) or any(star_active(st, [event], now) for st in aud.get("stars", []))


def defer_until(now: datetime, quiet: dict) -> datetime:
    """P4-D-123: a push due inside the quiet-hours window waits for its end (IST); otherwise it is due now."""
    if not quiet.get("enabled"):
        return now
    h1, m1 = map(int, quiet["start"].split(":"))
    h2, m2 = map(int, quiet["end"].split(":"))
    start, end = time(h1, m1), time(h2, m2)
    if start == end:
        return now
    t = now.astimezone(IST)
    inside = (start <= t.time() < end) if start < end else (t.time() >= start or t.time() < end)
    if not inside:
        return now
    due = datetime.combine(t.date(), end, tzinfo=IST)
    if due <= t:
        due += timedelta(days=1)
    return due.astimezone(timezone.utc)


def message_text(change_type: str, event: dict, team_name: str | None) -> tuple[str, str]:
    """Course and event info only, built from the event's current state: never a name, never message text (P4-D-137)."""
    title = f"{event['title']} {VERBS.get(change_type, 'updated')}"
    if change_type == "cancelled":
        return title, f"No longer happening{f' · {team_name}' if team_name else ''}"
    start = event.get("starts_at")
    a = anchor(event)
    parts = []
    if a:
        d = (start or a).astimezone(IST)
        parts.append(d.strftime("%a %-d %b") + (f" {d.strftime('%H:%M')}" if start else ""))
    if event.get("venue"):
        parts.append(event["venue"])
    body = ("→ " + ", ".join(parts)) if parts else "Open Skim for details"
    return title, body + (f" · {team_name}" if team_name else "")


def to_messages(tokens: list[str], title: str, body: str, data: dict) -> list[dict]:
    return [{"to": t, "title": title, "body": body, "data": data, "sound": "default", "channelId": "changes",
             "priority": "high", "ttl": 12 * 3600} for t in tokens]


# ---------------------------------------------------------------- Expo
class ExpoError(Exception):
    """`transient`: worth retrying on the next run (network, 429, 5xx); otherwise the request itself was refused."""

    def __init__(self, reason: str, transient: bool) -> None:
        super().__init__(reason)
        self.reason, self.transient = reason, transient


class ExpoClient:
    def __init__(self, access_token: str | None = None, base_url: str = "https://exp.host/--/api/v2/push",
                 session: aiohttp.ClientSession | None = None) -> None:
        self._token, self._url, self._session = access_token, base_url.rstrip("/"), session
        self._own = session is None

    async def close(self) -> None:
        if self._own and self._session is not None:
            await self._session.close()

    async def _post(self, path: str, payload: Any) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"  # enhanced push security
        try:
            async with self._session.post(f"{self._url}/{path}", data=json.dumps(payload), headers=headers) as r:
                if r.status == 429 or r.status >= 500:
                    raise ExpoError(f"expo_http_{r.status}", True)
                if r.status >= 400:
                    raise ExpoError(f"expo_http_{r.status}", False)
                doc = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ExpoError(type(e).__name__, True) from e
        if not isinstance(doc, dict) or "data" not in doc:
            raise ExpoError("expo_bad_response", True)
        return doc["data"]

    async def send(self, messages: list[dict]) -> list[dict]:
        """One request of at most BATCH messages -> one ticket per message, in order."""
        tickets = await self._post("send", messages)
        if not isinstance(tickets, list) or len(tickets) != len(messages):
            raise ExpoError("expo_ticket_mismatch", True)
        return tickets

    async def receipts(self, ids: list[str]) -> dict[str, dict]:
        data = await self._post("getReceipts", {"ids": ids})
        return data if isinstance(data, dict) else {}

"""Ingestion pipeline (raw-first, D-006) and the SDK AgentApplication wiring."""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable

from microsoft_agents.hosting.core import AgentApplication, MemoryStorage as SdkMemoryStorage, TurnContext, TurnState
from microsoft_agents.hosting.core.authorization import Connections

from .config import Settings
from .normalize import Normalized, log_line, normalize, raw_fields
from .pairing import pairing_code
from .storage.base import Storage

log = logging.getLogger("skim")
activity_log = logging.getLogger("skim.activity")

Sender = Callable[[TurnContext, str], Awaitable[None]]


@dataclass
class Capture:
    raw_id: int
    inserted: bool  # False => Teams retried a delivery we already have
    normalized: Normalized | None


# Set by the HTTP handler before the SDK runs the turn; read by the reply handlers.
current_capture: ContextVar[Capture | None] = ContextVar("current_capture", default=None)


def setup_activity_logger() -> None:
    """FR-12: the per-activity JSON line goes to stdout, bare, so it stays parseable."""
    if activity_log.handlers:
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    activity_log.addHandler(h)
    activity_log.setLevel(logging.INFO)
    activity_log.propagate = False


async def _persist(storage: Storage, settings: Settings, n: Normalized, raw_id: int) -> None:
    if n.team:
        await storage.upsert_team(n.team)
    if n.conversation:
        await storage.upsert_conversation(n.conversation)
    if n.install:
        if n.install["action"] == "add":
            await storage.mark_team_installed(
                n.install["team_id"], pairing_code(settings.pairing_secret, n.install["team_id"]),
                n.install["by_aad"], n.install["at"])
        else:
            await storage.mark_team_removed(n.install["team_id"], n.install["at"])
    if n.message_event:
        known = await storage.apply_message_event(n.message_event, raw_id)
        if not known:
            log.warning("original message missing for %s event: conversation=%s message=%s raw_id=%s",
                        n.message_event.event, n.message_event.conversation_id, n.message_event.message_id, raw_id)
        if n.attachments:
            await storage.add_attachments(n.attachments, raw_id)
    if n.reactions:
        await storage.add_reactions(n.reactions, raw_id)


async def ingest(storage: Storage, settings: Settings, activity: dict) -> Capture:
    """Store raw first; normalisation failures are logged and never undo the raw row."""
    raw_id, inserted = await storage.insert_raw(raw_fields(activity))
    try:
        activity_log.info(json.dumps(log_line(activity, raw_id, settings.log_message_text), ensure_ascii=False))
    except Exception:
        log.exception("could not build activity log line raw_id=%s", raw_id)
    normalized = None
    if inserted:
        try:
            normalized = normalize(activity)
            await _persist(storage, settings, normalized, raw_id)
        except Exception:
            log.exception("normalisation failed for raw_id=%s", raw_id)
    return Capture(raw_id, inserted, normalized)


async def _default_sender(context: TurnContext, text: str) -> None:
    await context.send_activity(text)


def welcome_text(code: str) -> str:
    return (
        "Hi! I'm Skim · Course Updates. I quietly keep a record of announcements posted in this team "
        "so they're easy to skim later. I won't post here again.\n\n"
        f"Pairing code: {code}"
    )


def build_agent(settings: Settings, connection_manager: Connections, sender: Sender | None = None) -> AgentApplication:
    send = sender or _default_sender
    app = AgentApplication[TurnState](
        storage=SdkMemoryStorage(),  # SDK turn state only; unused by Skim
        connection_manager=connection_manager,
        start_typing_timer=False,  # D-007: never emit anything into a channel
        **settings.sdk_config.get("AGENTAPPLICATION", {}),
    )

    async def _safe_send(context: TurnContext, text: str) -> None:
        try:
            await send(context, text)
        except Exception:
            log.exception("failed to send reply")

    @app.activity("installationUpdate")
    async def on_install(context: TurnContext, state: TurnState):
        cap = current_capture.get()
        inst = cap and cap.inserted and cap.normalized and cap.normalized.install
        if inst and inst["action"] == "add":
            await _safe_send(context, welcome_text(pairing_code(settings.pairing_secret, inst["team_id"])))

    @app.activity("message")
    async def on_message(context: TurnContext, state: TurnState):
        cap = current_capture.get()
        n = cap and cap.inserted and cap.normalized
        if n and n.conversation and n.conversation.get("conversation_type") == "personal":
            await _safe_send(context, f"Logged ✅ ({len(n.attachments)} attachment(s))")

    @app.error
    async def on_error(context: TurnContext, err: Exception):
        # FR-15: never surface errors to Teams; the raw row is already stored.
        log.error("turn error: %s", type(err).__name__, exc_info=err)

    return app

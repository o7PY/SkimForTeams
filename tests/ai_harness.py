"""Shared harness: a MemoryStorage team + channel, messages posted in order, a real Pipeline on a scripted provider."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ai_fakes import ScriptedProvider
from skim.ai.config import AIConfig
from skim.ai.pipeline import Pipeline
from skim.ai.providers.router import Router
from skim.storage.memory import MemoryStorage

IST = ZoneInfo("Asia/Kolkata")
CONV = "19:chan@thread.tacv2"
CFG = AIConfig(enabled=True, groq_api_key="k", gemini_api_key="k", groq_model="m", prompt_version="p3-test")


def ist(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


class Harness:
    def __init__(self, script: dict, cfg: AIConfig = CFG):
        self.store = MemoryStorage()
        self.provider = ScriptedProvider(script)
        self.router = Router([self.provider])
        self.pipeline = Pipeline(self.store, self.router, cfg)
        self.n = 0

    async def setup(self):
        s = self.store
        await s.upsert_team({"id": "T1", "aad_group_id": "g1", "name": "MA 221", "tenant_id": "x"})
        await s.mark_team_installed("T1", "ABC123", "prof-aad", datetime(2026, 10, 1, tzinfo=timezone.utc))
        await s.upsert_conversation({"id": CONV, "conversation_type": "channel", "team_id": "T1", "channel_id": CONV, "name": "General"})
        return self

    async def post(self, when: str, text: str, author="prof", subject=None, name="Prof. Menon", aad=None, mid=None):
        self.n += 1
        mid = mid or f"m{self.n}"
        await self.store.backfill_message({
            "conversation_id": CONV, "message_id": mid, "reply_to_id": None,
            "from_aad_id": aad or ("prof-aad" if author == "prof" else "student-aad"), "from_name": name, "text": text,
            "html": None, "importance": None, "mentions": None, "subject": subject, "created_at": ist(when),
            "last_edited_at": None, "deleted_at": None}, [], [])
        return mid

    async def run(self):
        outs = []
        for m in await self.store.list_pending_messages(self.pipeline.prompt_version, 3, 1000):
            m["author_role"] = "staff" if m["from_aad_id"] == "prof-aad" else "student"
            outs.append((m["message_id"], await self.pipeline.process(m)))
        return outs

    async def events(self):
        return await self.store.list_events(team_id="T1")

    async def changes(self, eid):
        return [c["change_type"] for c in (await self.store.get_event(eid))["changes"]]

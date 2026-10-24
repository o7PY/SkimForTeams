"""P2-D-140: live messages that arrived empty (file uploads) are refilled from the Graph copy, fill-only."""
import json
from pathlib import Path

from fake_graph import FakeGraph
from skim.bot import ingest
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.link_attachments import link_attachments
from skim.jobs.refill import refill_empty
from skim.normalize_graph import missing_attachments, normalize_url

FIX = Path(__file__).parent / "fixtures"
TEAM, CH, GID = "19:teamabc123@thread.tacv2", "19:chanGeneral@thread.tacv2", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MID = "1700000000050"
PDF = "Lecture11_Covariance_Correlation_Notes.pdf"
WEB = f"https://example.sharepoint.com/sites/Team/Shared%20Documents/General/{PDF}"
SETTINGS = Settings.from_env({"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true",
                              "STORAGE": "memory", "PAIRING_SECRET": "s"})


def graph_copy():
    return json.loads((FIX / "graph" / "chatMessage_text_with_pdf.json").read_text())


async def seed(store):
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    await store.upsert_channel({"id": CH, "team_id": TEAM, "display_name": "General", "membership_type": "standard"})
    await ingest(store, SETTINGS, json.loads((FIX / "channel_post_empty_with_pdf.json").read_text()))


async def run_refill(store, fake):
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(store, g, Budget(60))
        await refill_empty(ctx, {"id": TEAM, "aad_group_id": GID})
        await g.close()
    return ctx.stats


def test_missing_attachments_matches_on_type_and_normalised_url_only():
    have = [{"content_type": "text/html", "name": None, "content_url": None}]
    ref = {"content_type": "reference", "name": "A.pdf", "content_url": "https://h/sites/T/Shared Documents/A.pdf"}
    assert missing_attachments(have, [ref]) == [ref]
    same = {**ref, "content_url": "https://H/sites/T/Shared%20Documents/A.pdf?web=1"}
    assert missing_attachments(have + [ref], [same]) == []


async def test_empty_live_post_gets_its_text_subject_and_file_reference_from_graph(store):
    await seed(store)
    row = (await store.list_empty_messages(TEAM, 10))
    assert [r["message_id"] for r in row] == [MID]  # arrived with text=null
    fake = FakeGraph()
    fake.add(f"/teams/{GID}/channels/{CH}/messages/{MID}", graph_copy())
    stats = await run_refill(store, fake)
    assert stats["empty_messages_refilled"] == 1
    assert await store.list_empty_messages(TEAM, 10) == []
    full = await store.get_message(CH, MID)
    m = full["message"]
    assert m["text"] == "Notes for lecture 11, read before Monday" and m["subject"] == "Lecture 11 notes"
    assert m.get("source", "live") == "live" and m["current_version"] == 1 and len(full["versions"]) == 1  # still the live row, only its empty columns filled
    assert [a["name"] for a in full["attachments"] if a["content_type"] == "reference"] == [PDF]
    # linking now finds the document through the new reference attachment
    doc = await store.upsert_document({"drive_id": "d", "item_id": "i", "team_id": TEAM, "channel_id": CH, "name": PDF,
                                       "parent_item_id": "F", "path": PDF, "web_url": WEB,
                                       "web_url_norm": normalize_url(WEB)})
    ctx = JobContext(store, GraphClient(StaticTokenProvider()), Budget(60))
    await link_attachments(ctx, TEAM)
    assert [(l["message_id"], l["match_method"]) for l in await store.list_document_links(doc)][-1][0] == MID


async def test_refill_is_fill_only_and_idempotent(store):
    await seed(store)
    fake = FakeGraph()
    fake.add(f"/teams/{GID}/channels/{CH}/messages/{MID}", graph_copy())
    await run_refill(store, fake)
    stats = await run_refill(store, fake)  # nothing empty any more: no request, no change
    assert not stats and fake.count(f"/teams/{GID}/channels/{CH}/messages/{MID}") == 1

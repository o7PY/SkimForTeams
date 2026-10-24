"""T9: attachment -> document linking (FR-206): web_url first, name+path fallback; runs against memory and Postgres."""
import json
from datetime import datetime, timezone
from pathlib import Path

from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.link_attachments import link_attachments, relative_path_under_folder
from skim.normalize_graph import chat_message_row, normalize_url

TEAM, CH, OTHER = "19:team@thread.tacv2", "19:chan@thread.tacv2", "19:other@thread.tacv2"
DRIVE = "b!drive"
SITE = "https://example.sharepoint.com/sites/Team/Shared%20Documents/General"
FIX = Path(__file__).parent / "fixtures" / "graph"


async def seed(s):
    await s.upsert_team({"id": TEAM, "aad_group_id": "g"})
    await s.upsert_channel({"id": CH, "team_id": TEAM, "display_name": "General", "membership_type": "standard"})


async def add_doc(s, item, path, web_url, channel=CH, deleted=False):
    doc_id = await s.upsert_document({
        "drive_id": DRIVE, "item_id": item, "team_id": TEAM, "channel_id": channel, "name": path.split("/")[-1],
        "parent_item_id": "F", "path": path, "web_url": web_url, "web_url_norm": normalize_url(web_url),
        "deleted_at": datetime(2026, 10, 1, tzinfo=timezone.utc) if deleted else None})
    return doc_id


async def post(s, mid, url, name="x.pdf", conv=CH, content_type="reference"):
    await s.backfill_message({"conversation_id": conv, "message_id": mid, "reply_to_id": None, "from_aad_id": None,
                              "from_name": None, "text": "t", "html": None, "importance": None, "mentions": None,
                              "subject": None, "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
                              "last_edited_at": None, "deleted_at": None},
                             [{"conversation_id": conv, "message_id": mid, "content_type": content_type, "name": name,
                               "content_url": url, "payload": {"id": mid, "contentUrl": url}}], [])


async def link(s, team=None):
    ctx = JobContext(s, GraphClient(StaticTokenProvider()), Budget(60))
    await link_attachments(ctx, team)
    return ctx.stats


async def links_of(s, doc_id):
    return [(l["message_id"], l["match_method"]) for l in await s.list_document_links(doc_id)]


def test_relative_path_under_folder():
    f = relative_path_under_folder
    assert f(f"{SITE}/Lectures/week%203.pdf", "General") == "Lectures/week 3.pdf"
    assert f(f"{SITE}/syllabus.pdf", "general") == "syllabus.pdf"
    assert f(f"{SITE}/Lectures/week3.pdf", "Other") is None and f(f"{SITE}/syllabus.pdf", None) is None
    assert f("https://x/sites/T/General", "General") is None  # the folder itself, no file below it


async def test_matches_by_normalised_web_url_ignoring_case_encoding_query_and_slash(store):
    await seed(store)
    d = await add_doc(store, "a", "syllabus.pdf", f"{SITE}/syllabus.pdf")
    await post(store, "m1", "HTTPS://Example.SharePoint.com/sites/Team/Shared%20Documents/GENERAL/Syllabus.pdf?web=1#x")
    stats = await link(store)
    assert await links_of(store, d) == [("m1", "url")] and stats["links_url"] == 1


async def test_falls_back_to_name_and_path_when_the_url_differs(store):
    await seed(store)
    d = await add_doc(store, "w3", "Lectures/week3.pdf", "https://example.sharepoint.com/:b:/s/Team/EaBcDeF")  # sharing-style webUrl
    await post(store, "m2", f"{SITE}/lectures/WEEK3.pdf")
    stats = await link(store)
    assert await links_of(store, d) == [("m2", "name_path")] and stats["links_name_path"] == 1


async def test_web_url_wins_over_name_path(store):
    await seed(store)
    right = await add_doc(store, "r", "Lectures/week3.pdf", f"{SITE}/Lectures/week3.pdf")
    wrong = await add_doc(store, "w", "week3.pdf", "https://elsewhere.example/x")
    await post(store, "m3", f"{SITE}/Lectures/week3.pdf")
    await link(store)
    assert await links_of(store, right) == [("m3", "url")] and await links_of(store, wrong) == []


async def test_unmatched_is_left_alone_then_linked_once_the_file_appears_and_never_duplicated(store):
    await seed(store)
    await post(store, "m4", f"{SITE}/later.pdf")
    stats = await link(store)
    assert stats["attachments_unmatched"] == 1 and stats["links_url"] == 0
    d = await add_doc(store, "l", "later.pdf", f"{SITE}/later.pdf")
    await link(store)
    stats = await link(store)  # third run: nothing left to do
    assert await links_of(store, d) == [("m4", "url")] and stats["attachments_considered"] == 0


async def test_live_document_preferred_over_a_deleted_one_and_deleted_still_links(store):
    await seed(store)
    old = await add_doc(store, "old", "a.pdf", f"{SITE}/a.pdf", deleted=True)
    new = await add_doc(store, "new", "a.pdf", f"{SITE}/a.pdf")
    await post(store, "m5", f"{SITE}/a.pdf")
    await post(store, "m6", f"{SITE}/gone.pdf")
    gone = await add_doc(store, "gone", "gone.pdf", f"{SITE}/gone.pdf", deleted=True)
    await link(store)
    assert await links_of(store, new) == [("m5", "url")] and await links_of(store, old) == []
    assert await links_of(store, gone) == [("m6", "url")]


async def test_ignores_non_file_attachments_dm_conversations_and_other_teams(store):
    await seed(store)
    await store.upsert_team({"id": "19:t2@thread.tacv2", "aad_group_id": "g2"})
    await store.upsert_channel({"id": OTHER, "team_id": "19:t2@thread.tacv2", "display_name": "General", "membership_type": "standard"})
    d = await add_doc(store, "a", "a.pdf", f"{SITE}/a.pdf")
    await post(store, "card", None, content_type="application/vnd.microsoft.card.adaptive")
    await post(store, "dm", f"{SITE}/a.pdf", conv="a:dm-conversation")
    await post(store, "t2", f"{SITE}/a.pdf", conv=OTHER)
    stats = await link(store, TEAM)
    assert stats["attachments_considered"] == 0 and await links_of(store, d) == []
    stats = await link(store)  # no team filter: the other team's attachment is considered, finds no document of its team
    assert stats["attachments_considered"] == 1 and stats["attachments_unmatched"] == 1 and await links_of(store, d) == []


async def test_graph_fixture_attachment_links_to_its_document(store):
    """Real shape from the spike: contentType=reference, SharePoint contentUrl, no driveItem id."""
    await seed(store)
    d = await add_doc(store, "syl", "syllabus.pdf", "https://example.sharepoint.com/sites/Team/Shared Documents/General/syllabus.pdf")
    row = chat_message_row(json.loads((FIX / "chatMessage_reference_attachment.json").read_text()), CH)
    await store.backfill_message(row["message"], row["attachments"], row["reactions"])
    await link(store)
    assert [m for m, _ in await links_of(store, d)] == [row["message"]["message_id"]]


async def test_name_path_fallback_rejects_a_lookalike_url_on_another_host(store):
    """Attachment URLs are author-controlled: same folder/file path on a foreign host must not link."""
    await seed(store)
    d = await add_doc(store, "w3", "Lectures/week3.pdf", "https://example.sharepoint.com/:b:/s/Team/EaBcDeF")
    await post(store, "evil", "https://evil.example.net/sites/Team/Shared%20Documents/General/Lectures/week3.pdf")
    await post(store, "good", f"{SITE}/Lectures/week3.pdf")
    stats = await link(store)
    assert await links_of(store, d) == [("good", "name_path")] and stats["links_rejected_host"] == 1
    assert stats["attachments_unmatched"] == 1


# ------------------------------------------------------------------ P2-D-141: URL formats, history, item id
import pytest
from fake_graph import FakeGraph
from skim.jobs.link_attachments import history_index, historical_urls, shares_id


def test_normalize_url_handles_raw_spaces_sharing_prefix_port_and_slashes():
    a = normalize_url("https://Example.sharepoint.com/sites/Team/Shared Documents/General/appPackage.zip")
    assert a == normalize_url("https://example.sharepoint.com/sites/Team/Shared%20Documents/General/appPackage.zip")
    assert a == normalize_url("https://example.sharepoint.com:443/:u:/r/sites/Team/Shared%20Documents//General/AppPackage.zip?csf=1&web=1#x")
    assert normalize_url("https://example.sharepoint.com/:u:/s/Team/EaBc") != a  # opaque token links are not rewritten
    assert normalize_url("not a url") is None and normalize_url(None) is None


def test_historical_urls_from_rename_and_move_events():
    cur = normalize_url(f"{SITE}/Lectures/APPY.zip")
    assert historical_urls(cur, "Lectures/APPY.zip", "APPY.zip", "Lectures/appPackage.zip", "appPackage.zip") == [
        normalize_url(f"{SITE}/Lectures/appPackage.zip")] * 2
    assert historical_urls(cur, "Lectures/APPY.zip", "APPY.zip", "old/APPY.zip", None) == [normalize_url(f"{SITE}/old/APPY.zip")]
    assert historical_urls(None, "a", "a", "b", "b") == []


async def renamed_doc(s):
    """The live case: appPackage.zip was posted, then renamed to APPY.zip in the Files tab."""
    d = await add_doc(s, "zip", "APPY.zip", f"{SITE}/APPY.zip")
    await s.add_document_event({"document_id": d, "event": "renamed", "at": datetime(2026, 10, 3, tzinfo=timezone.utc),
                                "old_name": "appPackage.zip", "new_name": "APPY.zip",
                                "old_path": "appPackage.zip", "new_path": "APPY.zip"})
    return d


async def test_attachment_of_a_renamed_file_links_through_history(store):
    await seed(store)
    d = await renamed_doc(store)
    raw = "https://example.sharepoint.com/sites/Team/Shared Documents/General/appPackage.zip"  # raw spaces
    await post(store, "m7", raw, name="appPackage.zip")
    stats = await link(store)
    assert await links_of(store, d) == [("m7", "history")] and stats["links_history"] == 1
    assert normalize_url(raw) in await history_index(store, TEAM)


async def test_moved_file_links_through_history_and_a_live_document_beats_a_deleted_one(store):
    await seed(store)
    d = await add_doc(store, "mv", "Week1/notes.pdf", f"{SITE}/Week1/notes.pdf")
    await store.add_document_event({"document_id": d, "event": "moved", "at": datetime(2026, 10, 3, tzinfo=timezone.utc),
                                    "old_name": "notes.pdf", "new_name": "notes.pdf",
                                    "old_path": "notes.pdf", "new_path": "Week1/notes.pdf"})
    gone = await add_doc(store, "dead", "Week9/notes.pdf", f"{SITE}/Week9/notes.pdf", deleted=True)
    await store.add_document_event({"document_id": gone, "event": "moved", "at": datetime(2026, 10, 4, tzinfo=timezone.utc),
                                    "old_name": "notes.pdf", "new_name": "notes.pdf",
                                    "old_path": "notes.pdf", "new_path": "Week9/notes.pdf"})
    await post(store, "m8", f"{SITE}/notes.pdf", name="notes.pdf")
    await link(store)
    assert await links_of(store, d) == [("m8", "history")] and await links_of(store, gone) == []


async def link_with_graph(s, fake, team=None):
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(s, g, Budget(60))
        await link_attachments(ctx, team)
        await g.close()
    return ctx.stats


async def test_item_id_is_the_last_resort_and_only_for_documents_of_the_same_team(store):
    await seed(store)
    d = await add_doc(store, "item-1", "Quiz/q1.pdf", "https://example.sharepoint.com/:b:/s/Team/Opaque")
    url = "https://example.sharepoint.com/:b:/s/Team/EaSharingToken"  # a sharing link: path says nothing
    fake = FakeGraph()
    fake.add(f"/shares/{shares_id(url)}/driveItem", {"id": "item-1", "parentReference": {"driveId": DRIVE}})
    await post(store, "m9", url, name="q1.pdf")
    stats = await link_with_graph(store, fake)
    assert await links_of(store, d) == [("m9", "item_id")] and stats["links_item_id"] == 1


async def test_item_id_ignores_a_document_of_another_team_and_foreign_hosts(store):
    await seed(store)
    await store.upsert_team({"id": "19:t2@thread.tacv2", "aad_group_id": "g2"})
    await store.upsert_document({"drive_id": DRIVE, "item_id": "other", "team_id": "19:t2@thread.tacv2", "channel_id": None,
                                 "name": "x.pdf", "parent_item_id": "F", "path": "x.pdf",
                                 "web_url": "https://example.sharepoint.com/a", "web_url_norm": "https://example.sharepoint.com/a"})
    url = "https://example.sharepoint.com/:b:/s/Team/Tok"
    fake = FakeGraph()
    fake.add(f"/shares/{shares_id(url)}/driveItem", {"id": "other", "parentReference": {"driveId": DRIVE}})
    await post(store, "m10", url)
    await post(store, "m11", "https://evil.example.com/x.pdf")
    stats = await link_with_graph(store, fake)
    assert stats["links_item_id"] == 0 and stats["attachments_unmatched"] == 2 and stats["shares_calls"] == 1


async def test_item_id_403_is_logged_once_and_skipped_for_the_rest_of_the_run(store, caplog):
    await seed(store)
    fake = FakeGraph()
    for i in range(3):
        u = f"https://example.sharepoint.com/:b:/s/Team/T{i}"
        fake.add(f"/shares/{shares_id(u)}/driveItem", {"error": {"code": "accessDenied"}}, status=403)
        await post(store, f"mm{i}", u)
    with caplog.at_level("WARNING", logger="skim.jobs"):
        stats = await link_with_graph(store, fake)
    assert stats["shares_calls"] == 1 and stats["shares_forbidden"] == 1 and stats["attachments_unmatched"] == 3
    assert sum("Files.Read.All" in r.message for r in caplog.records) == 1


def test_shares_id_is_urlsafe_base64_with_u_prefix_and_encoded_spaces():
    i = shares_id("https://h.sharepoint.com/sites/T/Shared Documents/a.zip")
    assert i.startswith("u!") and "=" not in i and "/" not in i and "+" not in i
    import base64
    assert base64.urlsafe_b64decode(i[2:] + "==").decode() == "https://h.sharepoint.com/sites/T/Shared%20Documents/a.zip"

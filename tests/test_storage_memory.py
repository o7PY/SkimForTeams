from skim.bot import ingest
from skim.config import Settings
from skim.storage.memory import MemoryStorage

SETTINGS = Settings.from_env({
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true",
    "STORAGE": "memory", "PAIRING_SECRET": "s3cret"})
CONV, MSG = "19:chanGeneral@thread.tacv2", "1700000000001"


async def run(store, load, *names):
    for n in names:
        await ingest(store, SETTINGS, load(n))


async def test_create_edit_edit_delete_yields_one_message_four_versions(load):
    s = MemoryStorage()
    await run(s, load, "channel_post", "message_edit", "message_edit_2", "message_delete")
    assert len(s.messages) == 1
    m = s.messages[(CONV, MSG)]
    assert m["current_version"] == 4 and m["deleted_at"] is not None
    versions = [v for v in s.versions if v["message_id"] == MSG]
    assert [v["event"] for v in versions] == ["created", "edited", "edited", "deleted"]
    # history preserved
    assert versions[0]["text"].endswith("Nov 3 at 10am")
    assert versions[1]["text"].endswith("Nov 4 at 10am")
    assert versions[3]["text"].endswith("Nov 5 at 10am")  # delete keeps the last text
    assert len(s.raw) == 4


async def test_undelete_clears_deleted_at(load):
    s = MemoryStorage()
    await run(s, load, "channel_post", "message_delete", "message_undelete")
    m = s.messages[(CONV, MSG)]
    assert m["deleted_at"] is None and m["current_version"] == 3
    assert [v["event"] for v in s.versions] == ["created", "deleted", "undeleted"]


async def test_edit_for_unseen_message_creates_it(load):
    s = MemoryStorage()
    await run(s, load, "message_edit")
    assert s.messages[(CONV, MSG)]["text"].endswith("Nov 4 at 10am")
    assert s.messages[(CONV, MSG)]["current_version"] == 1
    assert [v["event"] for v in s.versions] == ["edited"]


async def test_duplicate_delivery_is_ignored(load):
    s = MemoryStorage()
    await run(s, load, "channel_post", "channel_post")
    assert len(s.raw) == 1 and len(s.messages) == 1 and len(s.versions) == 1


async def test_attachments_reactions_and_thread(load):
    s = MemoryStorage()
    await run(s, load, "channel_post", "channel_post_with_file", "reaction_added", "reaction_removed", "thread_reply")
    assert [a["name"] for a in s.attachments] == ["syllabus.pdf"]
    assert [r["action"] for r in s.reactions] == ["added", "removed"]
    assert s.messages[(CONV, "1700000000002")]["reply_to_id"] == MSG


async def test_install_remove_and_rename(load):
    s = MemoryStorage()
    await run(s, load, "install_add", "install_remove", "team_renamed")
    t = s.teams["19:teamabc123@thread.tacv2"]
    assert t["pairing_code"] and len(t["pairing_code"]) == 6
    assert t["name"] == "CS101 Autumn" and t["removed_at"] is not None and t["installed_at"] is not None
    assert t["aad_group_id"]


async def test_unknown_type_stored_raw_only(load):
    s = MemoryStorage()
    await run(s, load, "unknown_type")
    assert s.raw[0]["activity_type"] == "somethingNew" and not s.messages


async def test_normalisation_failure_keeps_raw(load, monkeypatch):
    import skim.bot as bot
    monkeypatch.setattr(bot, "normalize", lambda a: 1 / 0)
    s = MemoryStorage()
    cap = await bot.ingest(s, SETTINGS, load("channel_post"))
    assert len(s.raw) == 1 and cap.normalized is None

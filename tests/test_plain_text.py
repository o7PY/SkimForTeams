"""P2-D-144: messages.text is plain text; html stays as sent. Live ingest, Graph rows and the clean-up step."""
import json
from pathlib import Path

from skim.bot import ingest
from skim.config import Settings

FIX = Path(__file__).parent / "fixtures"
CH = "19:chanGeneral@thread.tacv2"
SETTINGS = Settings.from_env({"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true",
                              "STORAGE": "memory", "PAIRING_SECRET": "s"})


def rich_post(mid="1700000000070", text="Test 1: LNO<br>Test 2: AIDS<br>Test 3: ML"):
    a = json.loads((FIX / "channel_post.json").read_text())
    a.update(id=mid, text=text)
    a["attachments"] = [{"contentType": "text/html", "content": f"<div>{text}</div>"}]
    return a


async def test_live_post_text_is_plain_and_html_is_kept(store):
    await store.upsert_team({"id": "19:teamabc123@thread.tacv2", "aad_group_id": "g"})
    await ingest(store, SETTINGS, rich_post())
    full = await store.get_message(CH, "1700000000070")
    assert full["message"]["text"] == "Test 1: LNO\nTest 2: AIDS\nTest 3: ML"
    assert full["message"]["html"] == "<div>Test 1: LNO<br>Test 2: AIDS<br>Test 3: ML</div>"
    assert full["versions"][0]["text"] == "Test 1: LNO\nTest 2: AIDS\nTest 3: ML"


async def test_edit_with_markup_is_converted_too(store):
    await store.upsert_team({"id": "19:teamabc123@thread.tacv2", "aad_group_id": "g"})
    await ingest(store, SETTINGS, rich_post())
    edit = {**json.loads((FIX / "message_edit.json").read_text()), "id": "1700000000070",
            "text": "Moved to Friday<br>Room 4 &amp; 5"}
    await ingest(store, SETTINGS, edit)
    full = await store.get_message(CH, "1700000000070")
    assert full["message"]["text"] == "Moved to Friday\nRoom 4 & 5" and full["message"]["current_version"] == 2


async def test_fix_step_rederives_old_rows_and_is_idempotent(store):
    """Rows stored before the fix still hold tags in messages.text and message_versions.text."""
    await store.upsert_team({"id": "19:teamabc123@thread.tacv2", "aad_group_id": "g"})
    await ingest(store, SETTINGS, rich_post("m-ok", text="plain text"))
    await ingest(store, SETTINGS, rich_post("m-old"))
    # simulate the pre-fix state
    if hasattr(store, "messages"):
        store.messages[(CH, "m-old")]["text"] = "Test 1: LNO<br>Test 2: AIDS"
        [v.__setitem__("text", "Test 1: LNO<br>Test 2: AIDS") for v in store.versions if v["message_id"] == "m-old"]
    else:
        await store.pool.execute("update messages set text='Test 1: LNO<br>Test 2: AIDS' where message_id='m-old'")
        await store.pool.execute("update message_versions set text='Test 1: LNO<br>Test 2: AIDS' where message_id='m-old'")
    assert await store.fix_markup_texts(100) == 2  # the message and its one version
    full = await store.get_message(CH, "m-old")
    assert full["message"]["text"] == "Test 1: LNO\nTest 2: AIDS" and full["versions"][0]["text"] == "Test 1: LNO\nTest 2: AIDS"
    assert "<div>" in full["message"]["html"]  # html never rewritten
    assert (await store.get_message(CH, "m-ok"))["message"]["text"] == "plain text"
    assert await store.fix_markup_texts(100) == 0

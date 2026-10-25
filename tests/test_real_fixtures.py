"""Closes DIFF-008 / DIFF-017: the code is exercised with payload SHAPES captured from the real tenant and sanitised by
scripts/capture_fixtures.py (tests/fixtures/real/, reviewed by the human). No real text, names, ids or URLs are in them."""
import json
import re
from pathlib import Path

import pytest

from skim.normalize import normalize
from skim.normalize_graph import html_to_text, is_user_message, strip_download_urls

REAL = Path(__file__).parent / "fixtures" / "real"
FILES = sorted(REAL.glob("*.json"))


def test_there_are_real_fixtures_for_each_shape_group():
    names = {f.name.rsplit("_", 1)[0] for f in FILES}
    assert len(FILES) == 16
    assert {"graph_chatMessage_message_subject_edited", "graph_chatMessage_message_subject", "graph_chatMessage_message",
            "activity_message_-_channel", "activity_messageUpdate_editMessage_channel", "graph_channel"} <= names


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.name)
def test_real_fixtures_are_clean_json_with_no_secret_or_personal_looking_values(f):
    text = f.read_text()
    data = json.loads(text)
    assert isinstance(data, dict) and data
    assert not re.search(r"[\w.+-]+@(?!example\.invalid|thread)[\w-]+\.\w{2,}", text)               # no real e-mail
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I)  # no GUID
    assert not re.search(r"https?://(?!example\.invalid)", text.replace("schemas.microsoft.com", ""))  # no real host
    assert not re.search(r"https://example\.invalid/[0-9a-f]+ ", text)                                 # no URL tail
    assert not re.search(r"eyJ[\w-]{10,}|[A-Za-z0-9_+=/-]{40,}", text)                                 # no token-like blob
    assert "downloadUrl" not in text and "deltaLink" not in text


@pytest.mark.parametrize("f", [f for f in FILES if f.name.startswith("activity_")], ids=lambda f: f.name)
def test_real_activity_shapes_normalise_without_error(f):
    n = normalize(json.loads(f.read_text()))
    assert n is not None
    if "message" in f.name:
        assert n.message_event is not None and n.message_event.conversation_id


@pytest.mark.parametrize("f", [f for f in FILES if f.name.startswith("graph_chatMessage")], ids=lambda f: f.name)
def test_real_graph_messages_pass_the_message_helpers(f):
    msg = json.loads(f.read_text())
    assert strip_download_urls(msg) is not None
    if is_user_message(msg):
        assert isinstance(html_to_text((msg.get("body") or {}).get("content")) or "", str)
    else:
        assert msg.get("messageType") != "message"


def test_the_real_edited_message_with_a_subject_keeps_the_fields_the_enrichment_relies_on():
    msg = json.loads((REAL / "graph_chatMessage_message_subject_edited_1.json").read_text())
    assert msg["subject"] and msg["lastEditedDateTime"] and msg["messageType"] == "message"
    assert (msg.get("body") or {}).get("contentType") in ("html", "text")

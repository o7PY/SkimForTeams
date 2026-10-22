from skim.normalize import base_conversation_id, dedupe_key, log_line, normalize, parse_ts, raw_fields


def test_dedupe_key_ignores_key_order():
    assert dedupe_key({"a": 1, "b": 2}) == dedupe_key({"b": 2, "a": 1})
    assert dedupe_key({"a": 1}) != dedupe_key({"a": 2})


def test_channel_post(load):
    n = normalize(load("channel_post"))
    ev = n.message_event
    assert ev.event == "created" and ev.message_id == "1700000000001"
    assert ev.text == "Skim Midterm is on Nov 3 at 10am"  # <at> tags stripped
    assert "<b>Nov 3</b>" in ev.html
    assert ev.reply_to_id is None  # root post
    assert ev.mentions and ev.mentions[0]["type"] == "mention"
    assert n.attachments == []  # text/html lives on the message, not in attachments
    assert n.team["aad_group_id"] and n.conversation["conversation_type"] == "channel"
    assert n.conversation["team_id"] == n.team["id"]


def test_thread_reply_uses_channel_conversation_and_reply_to(load):
    ev = normalize(load("thread_reply")).message_event
    assert ev.conversation_id == "19:chanGeneral@thread.tacv2"  # ';messageid=' suffix stripped
    assert ev.reply_to_id == "1700000000001"


def test_reply_to_falls_back_to_thread_suffix(load):
    a = load("thread_reply")
    del a["replyToId"]
    assert normalize(a).message_event.reply_to_id == "1700000000001"


def test_group_chat_has_no_team(load):
    n = normalize(load("group_chat_message"))
    assert n.team is None and n.conversation["conversation_type"] == "groupChat"
    assert n.message_event.from_name == "Asha K"


def test_attachments(load):
    n = normalize(load("channel_post_with_file"))
    assert [a["name"] for a in n.attachments] == ["syllabus.pdf"]
    assert n.attachments[0]["content_url"].endswith("syllabus.pdf")
    assert n.message_event.html is not None
    p = normalize(load("personal_message_with_file"))
    assert p.attachments[0]["content_type"] == "application/vnd.microsoft.teams.file.download.info"


def test_edit_delete_undelete(load):
    assert normalize(load("message_edit")).message_event.event == "edited"
    assert normalize(load("message_delete")).message_event.event == "deleted"
    assert normalize(load("message_undelete")).message_event.event == "undeleted"


def test_reactions(load):
    added = normalize(load("reaction_added")).reactions
    removed = normalize(load("reaction_removed")).reactions
    assert [(r["action"], r["reaction_type"], r["message_id"]) for r in added] == [("added", "like", "1700000000001")]
    assert [r["action"] for r in removed] == ["removed"]


def test_install(load):
    assert normalize(load("install_add")).install["action"] == "add"
    assert normalize(load("install_remove")).install["action"] == "remove"
    a = load("install_add")
    a["action"] = "add-upgrade"
    assert normalize(a).install["action"] == "add"


def test_team_renamed(load):
    n = normalize(load("team_renamed"))
    assert n.team["name"] == "CS101 Autumn" and n.message_event is None


def test_unknown_and_garbage_do_not_raise(load):
    n = normalize(load("unknown_type"))
    assert n.message_event is None and n.reactions == []
    normalize({})
    normalize({"type": "message", "attachments": "nope", "entities": 5, "channelData": [], "conversation": "x"})


def test_raw_fields(load):
    r = raw_fields(load("message_edit"))
    assert r["activity_type"] == "messageUpdate" and r["event_type"] == "editMessage"
    assert r["payload"] == load("message_edit")
    assert raw_fields({})["activity_type"] == "unknown"


def test_parse_ts_is_utc():
    assert parse_ts("2026-10-20T09:00:00+05:30").isoformat() == "2026-10-20T03:30:00+00:00"
    assert parse_ts("2026-10-20T09:00:00Z").tzinfo is not None
    assert parse_ts("garbage").tzinfo is not None


def test_base_conversation_id():
    assert base_conversation_id("19:x@thread.tacv2;messageid=123") == "19:x@thread.tacv2"
    assert base_conversation_id(None) is None


def test_log_line(load):
    line = log_line(load("channel_post_with_file"), 7)
    assert line["raw_id"] == 7 and line["type"] == "message"
    assert "application/vnd.microsoft.teams.file.download.info" in line["attachment_types"]
    assert log_line(load("channel_post"), 1, include_text=False)["text"] is None
    assert len(log_line({"text": "x" * 500}, 1)["text"]) == 120


def test_install_from_members_added_bot(load):
    inst = normalize(load("members_added_bot")).install
    assert inst["action"] == "add" and inst["source"] == "membersAdded"
    assert inst["team_id"] == "19:teamabc123@thread.tacv2"


def test_members_added_other_user_or_personal_is_not_an_install(load):
    assert normalize(load("members_added_user")).install is None
    assert normalize(load("members_added_bot_personal")).install is None  # no team


def test_add_upgrade_is_an_install(load):
    inst = normalize(load("install_add_upgrade")).install
    assert inst["action"] == "add" and inst["source"] == "installationUpdate"

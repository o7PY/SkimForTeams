import validate_eval as v


def test_eval_set_is_valid():
    errors, _ = v.validate(v.load())
    assert errors == []


def test_eval_set_shape():
    rows = v.load()
    assert len({r["msg_id"] for r in rows}) == 54
    assert {r["author"] for r in rows} == {"prof", "student"}


def test_validator_catches_problems():
    rows = v.load()
    bad = [dict(r) for r in rows]
    bad[2]["text"] = bad[2]["text"].replace("Monday, 26 October", "Tuesday, 26 October")
    bad[3]["links_to"] = "99"
    errors, _ = v.validate(bad)
    assert any("is a monday" in e for e in errors) and any("links_to 99" in e for e in errors)

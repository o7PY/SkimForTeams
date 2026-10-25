import glob
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("capture_fixtures", Path(__file__).parent.parent / "scripts" / "capture_fixtures.py")
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)


def test_sanitiser_removes_identifying_data_and_keeps_shape():
    payload = {
        "type": "message",
        "from": {"name": "Asha Rao", "aadObjectId": "3f2b8c1e-1111-4222-8333-444455556666"},
        "text": "Quiz 2 on Friday, mail asha@iisc.ac.in or call +91 98765 43210",
        "timestamp": "2026-10-25T09:00:00.000Z",
        "attachments": [{"contentType": "text/html", "contentUrl": "https://iisc.sharepoint.com/sites/x/a.pdf?token=abc"}],
        "accessToken": "secret",
    }
    out = cf.Sanitiser().walk(payload)
    blob = json.dumps(out)
    assert "Asha" not in blob and "asha@" not in blob and "98765" not in blob and "sharepoint" not in blob
    assert "3f2b8c1e" not in blob and "secret" not in blob
    assert out["type"] == "message" and out["timestamp"] == payload["timestamp"]
    assert out["attachments"][0]["contentType"] == "text/html"
    assert cf.leaks(out) == []


def test_existing_fixtures_sanitise_cleanly():
    san = cf.Sanitiser()
    for f in glob.glob(str(Path(__file__).parent / "fixtures" / "**" / "*.json"), recursive=True):
        assert cf.leaks(san.walk(json.load(open(f)))) == [], f


def test_leak_scan_catches_real_looking_data():
    assert cf.leaks({"x": "write to bob@example.com"})
    assert cf.leaks({"x": "https://tenant.sharepoint.com/a"})


def test_url_with_spaces_is_replaced_entirely_and_a_leftover_tail_is_a_leak():
    """A real fixture kept 'https://example.invalid/<hash> Documents/General/<lecture>.pdf' (a SharePoint path with spaces)."""
    from capture_fixtures import Sanitiser, leaks
    out = Sanitiser().walk({"contentUrl": "https://t.sharepoint.com/sites/x/Shared Documents/General/Lecture11_Notes.pdf"})
    assert out["contentUrl"].startswith("https://example.invalid/") and " " not in out["contentUrl"] and "Lecture" not in str(out)
    assert leaks(out) == []
    assert leaks({"contentUrl": "https://example.invalid/abc123 Documents/General/Lecture11_Notes.pdf"})

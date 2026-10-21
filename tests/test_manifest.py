import json
import struct
import zipfile

import pytest

from build_manifest import RSC_PERMISSIONS, build

APP_ID = "12345678-1234-1234-1234-123456789abc"


def test_package_contents(tmp_path):
    out = tmp_path / "appPackage.zip"
    build(APP_ID, str(out))
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["color.png", "manifest.json", "outline.png"]
        m = json.loads(z.read("manifest.json"))
        sizes = {n: struct.unpack(">II", z.read(n)[16:24]) for n in ("color.png", "outline.png")}
    assert sizes == {"color.png": (192, 192), "outline.png": (32, 32)}
    assert m["manifestVersion"] == "1.17" and m["id"] == APP_ID
    assert m["name"]["short"] == "Skim · Course Updates" and len(m["name"]["short"]) <= 30
    bot = m["bots"][0]
    assert bot["botId"] == APP_ID and bot["supportsFiles"] is True
    assert set(bot["scopes"]) == {"team", "groupChat", "personal"}
    assert m["webApplicationInfo"]["id"] == APP_ID
    assert [p["name"] for p in m["authorization"]["permissions"]["resourceSpecific"]] == RSC_PERMISSIONS


def test_rejects_non_guid(tmp_path):
    with pytest.raises(SystemExit):
        build("not-a-guid", str(tmp_path / "x.zip"))

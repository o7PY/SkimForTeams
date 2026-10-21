"""Build appPackage.zip for sideloading into Teams (FR-13).

Usage: MICROSOFT_APP_ID=<bot app id> python scripts/build_manifest.py [--out appPackage.zip]
"""
import argparse
import json
import os
import re
import struct
import sys
import zipfile
import zlib

RSC_PERMISSIONS = [  # D-003
    "ChannelMessage.Read.Group",
    "ChannelMessage.Send.Group",
    "ChannelSettings.Read.Group",
    "TeamSettings.Read.Group",
    "TeamMember.Read.Group",
    "ChatMessage.Read.Chat",
]
GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def manifest(app_id: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": "0.1.0",
        "id": app_id,
        "developer": {  # placeholders: replace before publishing beyond the test tenant
            "name": "Skim",
            "websiteUrl": "https://example.com",
            "privacyUrl": "https://example.com/privacy",
            "termsOfUseUrl": "https://example.com/terms",
        },
        "name": {"short": "Skim · Course Updates", "full": "Skim · Course Updates"},
        "description": {
            "short": "Keeps a record of course announcements so students can skim them.",
            "full": "Skim quietly records messages posted in this team so important course updates "
                    "are easy to find later. It only posts once, when it is first added.",
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#4F46E5",
        "bots": [{
            "botId": app_id,
            "scopes": ["team", "groupChat", "personal"],
            "supportsFiles": True,
            "isNotificationOnly": False,
        }],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [],
        "webApplicationInfo": {"id": app_id, "resource": "https://RscBasedStoreApp"},
        "authorization": {"permissions": {"resourceSpecific": [
            {"name": n, "type": "Application"} for n in RSC_PERMISSIONS
        ]}},
    }


def _png(width: int, height: int, pixel) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def color_icon() -> bytes:
    """192x192: indigo square with a white 'S'-less bar motif (placeholder art)."""
    def px(x, y):
        bar = 48 <= x < 144 and (y in range(56, 72) or y in range(88, 104) or y in range(120, 136))
        return (255, 255, 255, 255) if bar else (79, 70, 229, 255)
    return _png(192, 192, px)


def outline_icon() -> bytes:
    """32x32: white bars on transparent."""
    def px(x, y):
        bar = 6 <= x < 26 and y in (9, 10, 15, 16, 21, 22)
        return (255, 255, 255, 255) if bar else (0, 0, 0, 0)
    return _png(32, 32, px)


def build(app_id: str, out: str) -> None:
    if not GUID.match(app_id):
        raise SystemExit("MICROSOFT_APP_ID must be a GUID")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(app_id), indent=2, ensure_ascii=False))
        z.writestr("color.png", color_icon())
        z.writestr("outline.png", outline_icon())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="appPackage.zip")
    args = ap.parse_args()
    app_id = os.environ.get("MICROSOFT_APP_ID", "").strip()
    if not app_id:
        sys.exit("MICROSOFT_APP_ID is not set")
    build(app_id, args.out)
    print(f"wrote {args.out}")

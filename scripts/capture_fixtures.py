"""Phase 3 T0: export SANITISED real payloads from the database into tests/fixtures/real/ (closes DIFF-008/017).

READ-ONLY (SELECTs only). The human runs it against Supabase (session pooler is fine):
    python scripts/capture_fixtures.py [--per-group 2] [--out tests/fixtures/real] [--dry]

What it keeps: the JSON *shape* (keys, nesting, types, enums, message types, event types, attachment content types).
What it replaces (stable, per-run pseudonyms so links between fields survive):
  - ids / GUIDs / long opaque tokens   -> id-<hash8>
  - person names, emails, phones       -> Person N / person-N@example.invalid / 0000000000
  - message text, html, subjects, file names, summaries -> "[text:<n> chars]" style placeholders
  - URLs                               -> https://example.invalid/<hash of path>
  - everything that looks like a token/secret is dropped
After sanitising it re-scans every output for anything that still looks like a leak (e-mail, GUID, URL with a real host,
long token) and refuses to write the file if it finds one. Review the files before committing them.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"https?://[^\s\"'<>]+")
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+=]{40,}")
PHONE = re.compile(r"(?<![\d:-])(\+?\d[\d\s().-]{8,}\d)(?![\d:TZ-])")
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?")

# Keys whose string values are free text or names: replaced by a length-preserving placeholder.
TEXT_KEYS = {"text", "content", "subject", "summary", "name", "title", "description", "textFormat_text", "topic"}
NAME_KEYS = {"displayName", "from_name", "givenName", "surname", "userPrincipalName", "mail", "email"}
SECRET_KEYS = {"token", "accessToken", "access_token", "secret", "password", "authorization", "@microsoft.graph.downloadUrl",
               "@odata.deltaLink", "@odata.nextLink", "deltaLink", "nextLink"}
# Keys whose values are enums/short labels we must keep verbatim (they carry the shape information).
KEEP_KEYS = {"type", "eventType", "messageType", "contentType", "importance", "membershipType", "conversationType",
             "channelType", "locale", "textFormat", "action", "role", "kind", "name_ext", "mimeType", "extension"}


class Sanitiser:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}
        self.people: dict[str, str] = {}

    def _id(self, v: str) -> str:
        return self.ids.setdefault(v, "id-" + hashlib.sha256(v.encode()).hexdigest()[:8])

    def _person(self, v: str) -> str:
        return self.people.setdefault(v, f"Person {len(self.people) + 1}")

    def _url(self, v: str) -> str:
        path = re.sub(r"^https?://[^/]+", "", v).split("?")[0]
        return "https://example.invalid/" + hashlib.sha256(path.encode()).hexdigest()[:10]

    def scalar(self, key: str | None, v: str) -> str:
        if key in KEEP_KEYS:
            return v
        if key and key.lower().endswith("name") and key not in TEXT_KEYS and key not in NAME_KEYS:
            return f"[name:{len(v)} chars]"
        if key in NAME_KEYS:
            return f"person-{self._person(v).split()[-1]}@example.invalid" if "@" in v else self._person(v)
        if key in TEXT_KEYS:
            return f"[text:{len(v)} chars]" if v else v
        if URL.fullmatch(v.strip()):
            return self._url(v.strip())
        v = EMAIL.sub(lambda m: f"person-{self._person(m.group(0)).split()[-1]}@example.invalid", v)
        v = GUID.sub(lambda m: self._id(m.group(0)), v)
        v = URL.sub(lambda m: self._url(m.group(0)), v)
        if not ISO_TS.fullmatch(v):
            v = PHONE.sub("0000000000", v)
        if LONG_TOKEN.fullmatch(v):
            return self._id(v)
        # Teams ids look like 19:abc...@thread.tacv2;messageid=123 -> hash each opaque part
        if v.startswith("19:") or "@thread" in v or re.match(r"^\d{1,2}:[\w-]{6,}", v):
            return self._id(v)
        if re.fullmatch(r"\d{10,}", v):  # message ids / epoch-ms ids
            return self._id(v)
        return v

    def walk(self, node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in SECRET_KEYS:
                    continue
                out[k] = self.walk(v, k)
            return out
        if isinstance(node, list):
            return [self.walk(x, key) for x in node]
        if isinstance(node, str):
            return self.scalar(key, node)
        return node


def leaks(obj: Any) -> list[str]:
    """Anything in the sanitised JSON that still looks real."""
    blob = json.dumps(obj, ensure_ascii=False)
    found = []
    for rx, label in ((EMAIL, "email"), (GUID, "guid"), (LONG_TOKEN, "long-token")):
        for m in rx.finditer(blob):
            s = m.group(0)
            if label == "email" and s.endswith("@example.invalid"):
                continue
            found.append(f"{label}:{s[:12]}…")
    for m in URL.finditer(blob):
        if not m.group(0).startswith("https://example.invalid/"):
            found.append(f"url:{m.group(0)[:30]}…")
    return found


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-group", type=int, default=2, help="max payloads per group (default 2)")
    ap.add_argument("--out", default="tests/fixtures/real")
    ap.add_argument("--dry", action="store_true", help="print the plan, write nothing")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set (put it in .env and `set -a; . ./.env; set +a`)", file=sys.stderr)
        return 2
    kw = {"statement_cache_size": 0} if ":6543" in dsn else {}
    conn = await asyncpg.connect(dsn, **kw)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        groups: dict[str, list[tuple[int, Any]]] = {}
        # raw_activities grouped by activity type / event type / reply / edit-ish shape
        for r in await conn.fetch(
            "select id, activity_type, event_type, payload from raw_activities order by id desc limit 2000"
        ):
            p = r["payload"] or {}
            tags = [r["activity_type"], r["event_type"] or "-"]
            if p.get("replyToId"):
                tags.append("reply")
            ctype = (p.get("conversation") or {}).get("conversationType")
            tags.append(ctype or "-")
            groups.setdefault("activity_" + "_".join(tags), []).append((r["id"], p))
        # graph_raw grouped by kind (+ messageType for chatMessage; reply vs top-level)
        for r in await conn.fetch("select id, kind, payload from graph_raw order by id desc limit 2000"):
            p = r["payload"] or {}
            tag = r["kind"]
            if r["kind"] == "chatMessage":
                tag += "_" + str(p.get("messageType")) + ("_reply" if p.get("replyToId") else "")
                if p.get("subject"):
                    tag += "_subject"
                if p.get("lastEditedDateTime"):
                    tag += "_edited"
            groups.setdefault("graph_" + tag, []).append((r["id"], p))

        san = Sanitiser()
        out_dir = Path(a.out)
        written, refused = 0, 0
        for name, items in sorted(groups.items()):
            for n, (rid, payload) in enumerate(items[: a.per_group], 1):
                clean = san.walk(payload)
                bad = leaks(clean)
                fn = out_dir / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}_{n}.json"
                if bad:
                    refused += 1
                    print(f"REFUSED {fn.name} (row {rid}): {bad[:3]}")
                    continue
                print(f"{'would write' if a.dry else 'write'} {fn}")
                if not a.dry:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    fn.write_text(json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
                written += 1
        print(f"\n{written} file(s) {'planned' if a.dry else 'written'}, {refused} refused as possible leaks, "
              f"{len(groups)} shape groups. REVIEW the files before committing.")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

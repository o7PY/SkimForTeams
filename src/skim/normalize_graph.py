"""Pure functions: Graph payload dicts -> rows. No I/O (keep it that way; Phase 3 builds on it)."""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from .normalize import parse_ts

_DOWNLOAD_URL_KEYS = ("@microsoft.graph.downloadUrl", "@content.downloadUrl")


def strip_download_urls(obj: Any) -> Any:
    """Remove pre-authenticated download URLs before anything is stored (spec §8)."""
    if isinstance(obj, dict):
        return {k: strip_download_urls(v) for k, v in obj.items() if k not in _DOWNLOAD_URL_KEYS}
    if isinstance(obj, list):
        return [strip_download_urls(v) for v in obj]
    return obj


def payload_hash(kind: str, payload: Any) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(f"{kind}\n{canon}".encode()).hexdigest()


_SHARING_PREFIX = re.compile(r"^/:[a-z]:/r(?=/)", re.I)  # /:x:/r/sites/T/... = a sharing link that carries the path
_SLASHES = re.compile(r"/{2,}")


def normalize_url(url: str | None) -> str | None:
    """Comparable form of a SharePoint URL (P2-D-122, P2-D-141): scheme+host lower-cased, default port dropped,
    path percent-decoded (so raw spaces == %20), lower-cased (SharePoint paths are case-insensitive), the
    `/:x:/r` sharing prefix removed, slashes collapsed, no query, no fragment, no trailing slash."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    host = parts.netloc.lower()
    if host.endswith(":443") and parts.scheme.lower() == "https":
        host = host[:-4]
    path = _SLASHES.sub("/", _SHARING_PREFIX.sub("", unquote(parts.path))).lower().rstrip("/")
    return f"{parts.scheme.lower()}://{host}{path}"


def _user(identity: Any) -> tuple[str | None, str | None]:
    u = (identity or {}).get("user") or {}
    return u.get("id"), u.get("displayName")


def drive_item_row(item: dict, drive_id: str | None = None) -> dict:
    """driveItem -> documents columns we can know from the item alone (no team/channel/path)."""
    parent = item.get("parentReference") or {}
    c_aad, c_name = _user(item.get("createdBy"))
    m_aad, m_name = _user(item.get("lastModifiedBy"))
    is_folder = "folder" in item
    return {
        "drive_id": parent.get("driveId") or drive_id,
        "item_id": item["id"],
        "name": item.get("name"),
        "parent_item_id": parent.get("id"),
        "is_folder": is_folder,
        "size_bytes": item.get("size"),
        "mime_type": None if is_folder else (item.get("file") or {}).get("mimeType"),
        "web_url": item.get("webUrl"),
        "web_url_norm": normalize_url(item.get("webUrl")),
        "etag": item.get("eTag"),
        "ctag": item.get("cTag"),
        "created_at": parse_ts(item["createdDateTime"]) if item.get("createdDateTime") else None,
        "created_by_aad": c_aad, "created_by_name": c_name,
        "modified_at": parse_ts(item["lastModifiedDateTime"]) if item.get("lastModifiedDateTime") else None,
        "modified_by_aad": m_aad, "modified_by_name": m_name,
    }


def join_path(base: str, name: str) -> str:
    return f"{base}/{name}" if base else name


# ---------------------------------------------------------------------------- chatMessage (T8, FR-205)
_AT_HTML = re.compile(r"<at\b[^>]*>.*?</at>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_BR = re.compile(r"<(br|/p|/div|/li)\b[^>]*>", re.I)
FILLABLE = ("reply_to_id", "from_aad_id", "from_name", "text", "html", "importance", "mentions", "subject",
            "created_at", "last_edited_at", "deleted_at")


def html_to_text(content: str | None) -> str | None:
    """Plain text of a Teams HTML body: @mentions dropped like the live path (strip_mentions), tags removed."""
    if content is None:
        return None
    s = _BR.sub("\n", _AT_HTML.sub("", content))
    s = html_lib.unescape(_TAGS.sub("", s))
    return re.sub(r"[ \t]+\n", "\n", s).strip()


def is_user_message(msg: dict) -> bool:
    """P2-D-142: a real message is messageType "message", or the new-to-the-API type "unknownFutureValue" when a
    user wrote it (Graph returns that value for types the v1.0 enum does not name yet). systemEventMessage and
    anything else, or an unknownFutureValue without a user author, is not a message (kept raw only)."""
    mtype = msg.get("messageType") or "message"
    if mtype == "message":
        return True
    return mtype == "unknownFutureValue" and bool((((msg.get("from") or {}).get("user")) or {}).get("id"))


def chat_message_row(msg: dict, conversation_id: str) -> dict | None:
    """One Graph chatMessage -> {"message", "attachments", "reactions"}; None for system events (member added,
    call ended, ...). The message dict has the `messages` columns that backfill may fill; `source` is 'graph'."""
    if not msg.get("id") or not is_user_message(msg):
        return None
    mid = str(msg["id"])
    frm = msg.get("from") or {}
    ident = frm.get("user") or frm.get("application") or frm.get("device") or {}
    body = msg.get("body") or {}
    content = body.get("content")
    is_html = (body.get("contentType") or "").lower() == "html"
    mentions = [m for m in (msg.get("mentions") or []) if isinstance(m, dict)]
    created = parse_ts(msg["createdDateTime"]) if msg.get("createdDateTime") else None
    edited = parse_ts(msg["lastEditedDateTime"]) if msg.get("lastEditedDateTime") else None
    deleted = parse_ts(msg["deletedDateTime"]) if msg.get("deletedDateTime") else None
    message = {
        "conversation_id": conversation_id, "message_id": mid, "reply_to_id": msg.get("replyToId") or None,
        "from_aad_id": ident.get("id"), "from_name": ident.get("displayName"),
        "text": html_to_text(content) if is_html else content, "html": content if is_html else None,
        "importance": msg.get("importance"), "mentions": mentions or None, "subject": msg.get("subject") or None,
        "created_at": created, "last_edited_at": edited, "deleted_at": deleted}
    attachments = [{"conversation_id": conversation_id, "message_id": mid, "content_type": a.get("contentType"),
                    "name": a.get("name"), "content_url": a.get("contentUrl"), "payload": a}
                   for a in msg.get("attachments") or [] if isinstance(a, dict)]
    reactions = []
    for r in msg.get("reactions") or []:
        user = ((r.get("user") or {}).get("user")) or {}
        reactions.append({"conversation_id": conversation_id, "message_id": mid, "user_aad_id": user.get("id"),
                          "user_name": user.get("displayName"), "reaction_type": r.get("reactionType"),
                          "action": "added", "at": parse_ts(r.get("createdDateTime"), created)})
    return {"message": message, "attachments": attachments, "reactions": reactions}


def fill_empty(before: dict, new: dict) -> dict:
    """P2-D-114: the columns where the stored (live) row is empty and the Graph message has a value. Nothing
    already present is ever overwritten."""
    return {k: new[k] for k in FILLABLE if before.get(k) is None and new.get(k) is not None}


def _attachment_key(a: dict) -> tuple:
    url = normalize_url(a.get("content_url"))
    return (a.get("content_type") or "", url or (a.get("name") or "").lower())


def missing_attachments(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """P2-D-114/140: Graph attachments the stored message does not have yet (same type and URL, or type and name
    when there is no URL). Existing rows are never changed or removed; a live post that arrived with only an empty
    text/html attachment still gets its file reference."""
    have = {_attachment_key(a) for a in existing}
    out = []
    for a in incoming:
        k = _attachment_key(a)
        if k not in have:
            have.add(k)
            out.append(a)
    return out

"""Pure functions: Graph payload dicts -> rows. No I/O (keep it that way; Phase 3 builds on it)."""
from __future__ import annotations

import hashlib
import json
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


def normalize_url(url: str | None) -> str | None:
    """Comparable form of a SharePoint URL (P2-D-122): percent-decoded, scheme+host+path lower-cased
    (SharePoint paths are case-insensitive), no query, no fragment, no trailing slash."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{unquote(parts.path).lower().rstrip('/')}"


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

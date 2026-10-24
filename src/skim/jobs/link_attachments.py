"""FR-206: link channel-post file attachments to `documents` rows (P2-D-122, P2-D-141, P2-DIFF-003).

Channel file attachments are contentType=reference with a SharePoint contentUrl and no driveItem id, so a link is
found by trying, in order (the first hit is recorded in match_method):
  url        normalised contentUrl == documents.web_url_norm within the team;
  history    the URL is where a document used to be: rebuilt from the rename/move events (old_path/old_name);
  name_path  the part of the URL path below the channel's folder == documents.path (case-insensitive), AND the URL's
             host is the document's own SharePoint host (an attachment is author-controlled data);
  item_id    last resort: Graph GET /shares/{encoded url}/driveItem gives drive_id + item_id, matched to a document
             of the same team. Needs Files.Read.All/Sites.Read.All; on 401/403 it is logged once and skipped for the
             rest of the run.
Unmatched attachments are simply retried on the next run.
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import quote, unquote, urlsplit

from ..graph.auth import GraphAuthError
from ..graph.client import GraphError, GraphNotFound
from ..normalize_graph import normalize_url
from .common import JobContext

log = logging.getLogger("skim.jobs")
LIMIT = 5000
SHARES_PER_RUN = 50


def historical_urls(web_url_norm: str | None, path: str | None, name: str | None,
                    old_path: str | None, old_name: str | None) -> list[str]:
    """Normalised URLs a document used to have, from one rename/move event. The channel-folder prefix of the
    current URL is kept; only the part below it (old_path) or the last segment (old_name) changes."""
    if not web_url_norm:
        return []
    out = []
    p = (path or "").lower()
    if old_path and p and web_url_norm.endswith("/" + p):
        out.append(web_url_norm[:-len(p)] + old_path.lower())
    if old_name and name and web_url_norm.endswith("/" + name.lower()):
        out.append(web_url_norm[:-len(name)] + old_name.lower())
    return out


async def history_index(store, team_id: str) -> dict[str, int]:
    """normalised old URL -> document id. A later event wins, and a live document wins over a deleted one."""
    index: dict[str, tuple[bool, int]] = {}
    for h in await store.list_document_history(team_id):
        for u in historical_urls(h["web_url_norm"], h["path"], h["name"], h["old_path"], h["old_name"]):
            live = h["deleted_at"] is None
            if u not in index or live or not index[u][0]:
                index[u] = (live, h["document_id"])
    return {u: d for u, (_, d) in index.items()}


def shares_id(url: str) -> str:
    """Graph /shares encoding: u! + url-safe base64 without padding (spaces must be %20 first)."""
    enc = base64.urlsafe_b64encode(quote(url.strip(), safe=":/%?&=#~@!$'()*+,;").encode()).decode().rstrip("=")
    return "u!" + enc


def relative_path_under_folder(content_url: str, folder_name: str | None) -> str | None:
    """The file's path relative to the channel's Files folder, read from a SharePoint URL: everything after the
    first path segment equal to the folder name (the folder is named after the channel). None if not derivable."""
    if not content_url or not folder_name:
        return None
    segs = [s for s in unquote(urlsplit(content_url).path).split("/") if s]
    want = folder_name.strip().lower()
    for i, seg in enumerate(segs):
        if i >= 1 and seg.lower() == want and i + 1 < len(segs):
            return "/".join(segs[i + 1:])
    return None


def _same_host(content_url: str, doc_url_norm: str | None) -> bool:
    return bool(doc_url_norm) and urlsplit(content_url).netloc.lower() == urlsplit(doc_url_norm).netloc


async def _resolve_share(ctx: JobContext, att: dict, state: dict) -> dict | None:
    """Method item_id: ask Graph which driveItem this URL is; only a document of the attachment's own team counts."""
    st = ctx.stats
    if not urlsplit(att["content_url"]).netloc.lower().endswith(".sharepoint.com"):
        return None  # never ask Graph to resolve an arbitrary link from a post
    if state["blocked"] or state["calls"] >= SHARES_PER_RUN or ctx.budget.expired():
        return None
    state["calls"] += 1
    st["shares_calls"] += 1
    try:
        item = await ctx.graph.get(f"/shares/{shares_id(att['content_url'])}/driveItem",
                                   {"$select": "id,parentReference"})
    except GraphNotFound:
        st["shares_not_found"] += 1
        return None
    except GraphAuthError as e:
        state["blocked"] = True
        st["shares_forbidden"] += 1
        log.warning("attachment linking by item id needs more Graph permission (Files.Read.All or Sites.Read.All): %s",
                    type(e).__name__)
        return None
    except GraphError:
        st["shares_errors"] += 1
        return None
    drive_id = (item.get("parentReference") or {}).get("driveId")
    doc = await ctx.storage.get_document(drive_id, item["id"]) if drive_id and item.get("id") else None
    return doc if doc and doc.get("team_id") == att["team_id"] else None


async def link_attachments(ctx: JobContext, team_id: str | None = None) -> None:
    st, store = ctx.stats, ctx.storage
    channels: dict[str, dict] = {}
    histories: dict[str, dict[str, int]] = {}
    shares = {"calls": 0, "blocked": False}
    for att in await store.list_unlinked_attachments(team_id, LIMIT):
        st["attachments_considered"] += 1
        ch = channels.get(att["conversation_id"]) or channels.setdefault(
            att["conversation_id"], next((c for c in await store.list_channels(att["team_id"])
                                         if c["id"] == att["conversation_id"]), {}))
        doc, method = None, None
        norm = normalize_url(att["content_url"])
        if norm:
            doc, method = await store.find_document_by_url(att["team_id"], norm), "url"
        if doc is None and norm:
            if att["team_id"] not in histories:
                histories[att["team_id"]] = await history_index(store, att["team_id"])
            did = histories[att["team_id"]].get(norm)
            doc, method = (await store.get_document_by_id(did) if did else None), "history"
        if doc is None:
            rel = relative_path_under_folder(att["content_url"], ch.get("display_name"))
            if rel:
                doc, method = await store.find_document_by_path(att["conversation_id"], rel), "name_path"
                if doc is not None and not _same_host(att["content_url"], doc.get("web_url_norm")):
                    # A post can carry any URL: a lookalike path on another host must not link to our file.
                    st["links_rejected_host"] += 1
                    doc = None
        if doc is None and norm:
            doc, method = await _resolve_share(ctx, att, shares), "item_id"
        if doc is None:
            st["attachments_unmatched"] += 1
            continue
        await store.add_attachment_link(att["id"], doc["id"], method)
        st[f"links_{method}"] += 1

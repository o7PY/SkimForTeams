"""FR-206: link channel-post file attachments to `documents` rows (P2-D-122, P2-DIFF-003).

Channel file attachments are contentType=reference with a SharePoint contentUrl and no driveItem id. Method 1:
normalised contentUrl == documents.web_url_norm within the team (`web_url`). Method 2: the part of the URL path
below the channel's folder name == documents.path within the channel (`name_path`, case-insensitive) AND the URL's
host is the document's own SharePoint host (an attachment is author-controlled data). Pure
database work (no Graph calls); unmatched attachments are simply retried on the next run.
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit

from ..normalize_graph import normalize_url
from .common import JobContext

LIMIT = 5000


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


async def link_attachments(ctx: JobContext, team_id: str | None = None) -> None:
    st, store = ctx.stats, ctx.storage
    channels: dict[str, dict] = {}
    for att in await store.list_unlinked_attachments(team_id, LIMIT):
        st["attachments_considered"] += 1
        ch = channels.get(att["conversation_id"]) or channels.setdefault(
            att["conversation_id"], next((c for c in await store.list_channels(att["team_id"])
                                         if c["id"] == att["conversation_id"]), {}))
        doc, method = None, None
        norm = normalize_url(att["content_url"])
        if norm:
            doc, method = await store.find_document_by_url(att["team_id"], norm), "web_url"
        if doc is None:
            rel = relative_path_under_folder(att["content_url"], ch.get("display_name"))
            if rel:
                doc, method = await store.find_document_by_path(att["conversation_id"], rel), "name_path"
                if doc is not None and not _same_host(att["content_url"], doc.get("web_url_norm")):
                    # A post can carry any URL: a lookalike path on another host must not link to our file.
                    st["links_rejected_host"] += 1
                    doc = None
        if doc is None:
            st["attachments_unmatched"] += 1
            continue
        await store.add_attachment_link(att["id"], doc["id"], method)
        st[f"links_{method}"] += 1

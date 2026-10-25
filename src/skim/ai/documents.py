"""Document text extraction (FR-306, P3-D-015). THIS IS THE ONLY MODULE ALLOWED TO DOWNLOAD FILE CONTENT.

Bytes are fetched with the app token from the Graph host only (`/drives/{d}/items/{i}/content`), follow Graph's 302 to the
pre-authenticated URL WITHOUT the bearer token, are capped by size, parsed in memory and dropped. Nothing is stored except
extracted text (document_texts / doc_chunks); the download URL is never logged or stored."""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import aiohttp

from ..textclean import clean_text

log = logging.getLogger("skim.ai")
EXTENSIONS = ("pdf", "docx", "pptx")
_ID = re.compile(r"[A-Za-z0-9!_.\-=$]{1,200}")  # Graph drive/item ids; nothing that could alter the URL path
MAX_ZIP_EXPANSION = 100_000_000  # zip-bomb guard: total uncompressed bytes allowed per docx/pptx


class DocumentSkip(Exception):
    """A permanent reason not to extract (recorded as status 'skipped'/'failed'); never carries content."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class TransientFetchError(Exception):
    """Network / throttling / 5xx: leave the document pending and try again next run."""


@dataclass
class Extracted:
    pages: list[str] = field(default_factory=list)   # one entry per PDF page / PPTX slide; DOCX is a single entry
    page_count: int = 0


def extension(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


async def fetch_content(graph, drive_id: str, item_id: str, max_bytes: int,
                        session: aiohttp.ClientSession | None = None) -> bytes:
    """Download one file. Raises DocumentSkip (too big / not found / no access) or TransientFetchError."""
    if not (_ID.fullmatch(drive_id or "") and _ID.fullmatch(item_id or "")):
        raise DocumentSkip("failed", "bad_id")
    url = f"{graph.base_url}/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/content"
    base = urlsplit(graph.base_url)
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
    try:
        token = await graph.tokens.get_token()
        async with session.get(url, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False) as r:
            if r.status in (301, 302, 303, 307, 308):
                target = urljoin(url, r.headers.get("Location", ""))
                parts = urlsplit(target)
                if not (parts.scheme == "https" or (parts.scheme, parts.netloc) == (base.scheme, base.netloc)):
                    raise DocumentSkip("failed", "bad_redirect")
                src = session.get(target)  # pre-authenticated: NO Authorization header goes to this host
            elif r.status == 200:
                return await _read_capped(r, max_bytes)
            else:
                raise _status_error(r.status)
            async with src as r2:
                if r2.status != 200:
                    raise _status_error(r2.status)
                return await _read_capped(r2, max_bytes)
    except (aiohttp.ClientError, TimeoutError) as e:
        raise TransientFetchError(type(e).__name__) from None
    finally:
        if own:
            await session.close()


def _status_error(status: int) -> Exception:
    if status in (404, 410):
        return DocumentSkip("failed", "not_found")
    if status in (401, 403):
        return DocumentSkip("failed", "no_access")
    return TransientFetchError(f"http_{status}")


async def _read_capped(r: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    if (r.content_length or 0) > max_bytes:
        raise DocumentSkip("skipped", "too_large")
    buf = bytearray()
    async for chunk in r.content.iter_chunked(64 * 1024):
        buf += chunk
        if len(buf) > max_bytes:
            raise DocumentSkip("skipped", "too_large")
    return bytes(buf)


# ------------------------------------------------------------------------------------------------ extraction
def extract(name: str | None, data: bytes, max_pages: int) -> Extracted:
    """bytes -> page texts. Raises DocumentSkip for unsupported / encrypted / corrupt / over-the-page-limit files."""
    ext = extension(name)
    try:
        if ext == "pdf":
            return _clean(_pdf(data, max_pages))
        if ext == "docx":
            return _clean(_docx(data))
        if ext == "pptx":
            return _clean(_pptx(data, max_pages))
    except DocumentSkip:
        raise
    except Exception as e:  # corrupt files raise all sorts of things; only the class name is kept
        raise DocumentSkip("failed", f"unreadable_{type(e).__name__}"[:60]) from None
    raise DocumentSkip("skipped", "unsupported_type")


def _clean(x: Extracted) -> Extracted:
    return Extracted([clean_text(p) for p in x.pages], x.page_count)  # NUL / lone surrogates would break the database


def _pdf(data: bytes, max_pages: int) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise DocumentSkip("skipped", "encrypted")
    n = len(reader.pages)
    if n > max_pages:
        raise DocumentSkip("skipped", "too_many_pages")
    return Extracted([(p.extract_text() or "").strip() for p in reader.pages], n)


def _zip(data: bytes) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > MAX_ZIP_EXPANSION:
        raise DocumentSkip("skipped", "too_large_expanded")
    return z


def _text_runs(xml: bytes, para: str, run: str) -> str:
    """Join text runs `run` per paragraph `para` (namespace-agnostic), one paragraph per line."""
    head = xml[:4096].lower()  # real Office XML never declares a DTD/entity; refusing them blocks XXE and billion-laughs
    if b"<!doctype" in head or b"<!entity" in xml.lower():
        raise DocumentSkip("failed", "xml_dtd_refused")
    lines = []
    for p in ET.fromstring(xml).iter():
        if p.tag.endswith("}" + para):
            lines.append("".join(t.text or "" for t in p.iter() if t.tag.endswith("}" + run)))
    return "\n".join(x for x in lines if x.strip())


def _docx(data: bytes) -> Extracted:
    z = _zip(data)
    return Extracted([_text_runs(z.read("word/document.xml"), "p", "t")], 1)


def _pptx(data: bytes, max_pages: int) -> Extracted:
    z = _zip(data)
    slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if len(slides) > max_pages:
        raise DocumentSkip("skipped", "too_many_pages")
    return Extracted([_text_runs(z.read(n), "p", "t") for n in slides], len(slides))

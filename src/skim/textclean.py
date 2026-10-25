"""Make text safe for Postgres (P3-DIFF-014). Postgres text/jsonb cannot hold NUL (\\x00) and asyncpg cannot encode lone
UTF-16 surrogates; either one raised CharacterNotInRepertoireError / UnicodeEncodeError from a database write.
Teams text, LLM output and PDF text can all contain them. This module is applied at every boundary: where text is extracted,
where LLM replies are parsed, and (as the last line of defence) on every value sent to the database."""
from __future__ import annotations

import re
from typing import Any

_BAD = re.compile("[\x00\ud800-\udfff]")


def clean_text(s: str) -> str:
    """Drop NUL and lone surrogates; everything else (including emoji and other control characters) is kept."""
    return _BAD.sub("", s) if _BAD.search(s) else s


def clean_json(o: Any) -> Any:
    """Recursively clean strings (and dict keys) inside JSON-like data."""
    if isinstance(o, str):
        return clean_text(o)
    if isinstance(o, dict):
        return {clean_text(k) if isinstance(k, str) else k: clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    return o


def clean_arg(o: Any) -> Any:
    """A query parameter: strings are cleaned, containers recursed; datetimes, numbers, bytes and None pass through."""
    if isinstance(o, str):
        return clean_text(o)
    if isinstance(o, (dict, list, tuple)):
        return clean_json(o)
    return o

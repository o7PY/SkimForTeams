"""Prompts are files in this directory (P3-D-006). Bump PROMPT_VERSION whenever any prompt text changes."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_VERSION = "p3-v2"
_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")

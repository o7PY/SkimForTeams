"""Privacy redaction before ANY LLM or embedding call (P3-D-004, FR-302).

Emails, phone numbers, roll/SR numbers and person names become placeholders ([PERSON_1], [EMAIL_1], ...). The mapping
exists only inside a Redactor instance (one per call), is never stored or logged, and `restore` reverses it on output.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# +91 98765 43210 / 9876543210 / 080-2293 2000 style; not dates (2026-10-26) or times.
_PHONE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,6}(?![\w-])")
_ROLL = re.compile(r"\b(?:(?:roll|sr|reg(?:istration)?)\s*(?:no\.?|number|#)?\s*[:\-]?\s*[A-Z]{0,4}\d[\d-]{3,}[A-Z\d]*"
                   r"|[A-Z]{2,4}\d{2}[A-Z]?\d{3,}[A-Z\d]*)\b", re.I)
_TITLED = re.compile(r"\b(?:Prof(?:essor)?|Dr|Mr|Mrs|Ms|Miss|Sir|Madam)\.?\s+(?:[A-Z][\w'-]+)(?:\s+(?:[A-Z]\.|[A-Z][\w'-]+))?")
_PHONE_MIN_DIGITS = 8


class Redactor:
    def __init__(self, names: list[str] | None = None) -> None:
        self._forward: dict[str, str] = {}   # original (case-folded) -> placeholder
        self._backward: dict[str, str] = {}  # placeholder -> first original spelling
        self._counts: dict[str, int] = {}
        self._names = self._name_variants(names or [])

    @staticmethod
    def _name_variants(names: list[str]) -> dict[str, str]:
        """variant -> canonical full name, so "Asha Rao", "Asha" and "Rao" share one placeholder."""
        out: dict[str, str] = {}
        for n in names:
            n = (n or "").strip()
            if len(n) < 2:
                continue
            out.setdefault(n, n)
            for part in (p for p in re.split(r"\s+", n) if len(p) > 2):  # first / last names alone also identify
                out.setdefault(part, n)
        return dict(sorted(out.items(), key=lambda kv: len(kv[0]), reverse=True))

    def _ph(self, kind: str, original: str) -> str:
        key = f"{kind}:{original.casefold()}"
        if key not in self._forward:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            ph = f"[{kind}_{self._counts[kind]}]"
            self._forward[key] = ph
            self._backward[ph] = original
        return self._forward[key]

    def redact(self, text: str | None) -> str:
        if not text:
            return text or ""
        text = _EMAIL.sub(lambda m: self._ph("EMAIL", m.group(0)), text)
        text = _ROLL.sub(lambda m: self._ph("ID", m.group(0)), text)
        text = _PHONE.sub(lambda m: self._ph("PHONE", m.group(0)) if sum(c.isdigit() for c in m.group(0)) >= _PHONE_MIN_DIGITS
                          and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(0)) else m.group(0), text)
        def titled(m):
            bare = re.sub(r"^\S+\s+", "", m.group(0))  # "Menon" alone is the same person later
            self._names = {**self._names, **{k: m.group(0) for k in self._name_variants([bare])}}
            return self._ph("PERSON", m.group(0))

        text = _TITLED.sub(titled, text)
        for name, canonical in sorted(self._names.items(), key=lambda kv: len(kv[0]), reverse=True):
            text = re.sub(rf"(?<![\w])(@?){re.escape(name)}(?![\w])",
                          lambda m: m.group(1) + self._ph("PERSON", canonical), text, flags=re.I)
        return text

    def restore(self, text: str | None) -> str | None:
        if text is None:
            return None
        for ph, original in self._backward.items():
            text = text.replace(ph, original)
        return text

    @property
    def placeholders(self) -> int:
        return len(self._backward)

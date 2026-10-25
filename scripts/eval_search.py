"""Phase 3 T10 search check: embed the 50 eval messages with the REAL Gemini embedding model, ask a few natural questions,
and report whether a message about the right event is in the top 3 (hit@3). Appends to Bookkeeping/PHASE3_EVAL.md.

    set -a; . ./.env; set +a
    python scripts/eval_search.py [--interval 2] [--dry] [--no-log]      # --dry = local hashing vectors, no network

Needs GEMINI_API_KEY (and optionally EMBED_MODEL, default gemini-embedding-001). Synthetic eval text only; nothing is stored."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src")]

from skim.ai.embeddings import STORED_DIMS, Embedder, message_text  # noqa: E402
from skim.ai.evaluate import load_rows  # noqa: E402
from skim.ai.providers.base import ProviderError  # noqa: E402
from skim.ai.providers.gemini import GeminiProvider  # noqa: E402

# (question, a substring of event_title that a correct hit's message is about)
QUERIES = [("when is the midsem", "midsem"), ("what is the date of quiz 2", "quiz 2"), ("assignment 1 deadline", "assignment 1"),
           ("when are office hours", "office hours"), ("project proposal submission", "project proposal"),
           ("final presentations schedule", "final presentations"), ("lab session problem set 3", "lab session"),
           ("when is the endsem exam", "endsem"), ("is there a makeup lecture", "makeup lecture"),
           ("quiz 3 date", "quiz 3")]


class HashProvider:
    model = "dry-hash"

    async def embed(self, model, texts, dims, task):
        out = []
        for t in texts:
            v = [0.0] * dims
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
            out.append(v)
        return out, None


def cos(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--log", default=str(ROOT / "Bookkeeping" / "PHASE3_EVAL.md"))
    ap.add_argument("--no-log", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not a.dry and not key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    model = "dry-hash" if a.dry else os.environ.get("EMBED_MODEL") or "gemini-embedding-001"
    prov = HashProvider() if a.dry else GeminiProvider(key, os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite")
    emb = Embedder(prov, model, STORED_DIMS, interval=0 if a.dry else a.interval)
    rows = load_rows(Path(os.environ.get("EVAL_CSV") or ROOT / "tests" / "eval" / "eval_messages.csv"))
    msgs = {}
    for r in rows:  # one entry per message; remember every event title it is about
        m = msgs.setdefault(r["msg_id"], {"text": message_text({"subject": r.get("subject"), "text": r["text"]}), "titles": set()})
        if r.get("event_title"):
            m["titles"].add(r["event_title"].lower())
    ids = list(msgs)
    try:
        vecs = await emb.embed_documents([msgs[i]["text"] for i in ids], [[] for _ in ids])
        hits_n, lines = 0, []
        for q, want in QUERIES:
            qv = await emb.embed_query(q)
            top = sorted(ids, key=lambda i: -cos(qv, vecs[ids.index(i)]))[:3]
            ok = any(want in t for i in top for t in msgs[i]["titles"])
            hits_n += ok
            lines.append(f"- {'HIT ' if ok else 'MISS'} {q!r}: top3 msg ids {top}")
    except ProviderError as e:
        print(f"embedding failed: {e.kind} {e.status or ''} (Gemini billing/quota? see PHASE3_DIFF-006)", file=sys.stderr)
        return 1
    finally:
        if not a.dry:
            await prov.close()
    head = (f"\n\n## Search check {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC{' (DRY, hashing vectors)' if a.dry else ''}\n"
            f"- embedding model: {model}, {STORED_DIMS} dims, {len(ids)} messages, {len(QUERIES)} questions\n"
            f"- hit@3: {hits_n}/{len(QUERIES)}\n")
    out = head + "\n".join(lines)
    print(out)
    if not a.no_log:
        with open(a.log, "a", encoding="utf-8") as f:
            f.write(out + "\n")
        print(f"\nappended to {a.log}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

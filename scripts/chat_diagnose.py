"""Why does "Ask the course" say it can't find something? Walks ONE question through every step against the REAL database, read-only:
your teams, your events, which events the question picks, which embeddings exist (per model), the top vector hits with their scores,
and finally the full answer. Nothing is written (not even the rate-limit row) and nothing is stored.

    set -a; . ./.env; set +a      # DATABASE_URL
    export GEMINI_API_KEY=... GROQ_API_KEY=... GROQ_MODEL=...     # the same values Cloud Run uses (needed to embed and to answer)
    python scripts/chat_diagnose.py                               # no arguments: lists the app users so you can pick yours
    python scripts/chat_diagnose.py --email you@example.com "when is the next quiz"
    python scripts/chat_diagnose.py --user-id <uuid> "how many quizzes are there next month"  [--item-id 123] [--no-answer]

This prints text from YOUR course (event titles, snippets) to YOUR terminal, which is the point; nothing is sent anywhere except the
question and redacted context to the model providers (as the app does) when it runs the final step (skip it with --no-answer)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src")]

from skim.ai.config import AIConfig  # noqa: E402
from skim.ai.providers.router import AllProvidersFailed  # noqa: E402
from skim.ai.services import AIServices  # noqa: E402
from skim.api.chat import EVENT_SCAN, MIN_SCORE, TOP_K, ChatService, event_line, pick_events  # noqa: E402
from skim.api import cards  # noqa: E402
from skim.eventtime import is_upcoming  # noqa: E402
from skim.storage.postgres import PostgresStorage  # noqa: E402


def head(t: str) -> None:
    print(f"\n=== {t}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--user-id")
    ap.add_argument("--email")
    ap.add_argument("--item-id", type=int)
    ap.add_argument("--no-answer", action="store_true", help="stop after retrieval (no model call)")
    a = ap.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set (set -a; . ./.env; set +a)", file=sys.stderr)
        return 2
    storage = PostgresStorage(os.environ["DATABASE_URL"])
    await storage.start()
    try:
        pool = storage.pool
        if not (a.user_id or a.email):
            head("app users (pick one: --user-id or --email)")
            for r in await pool.fetch("select id::text, email, name, created_at from app_users order by created_at"):
                print(f"  {r['id']}  {r['email']}  {r['name']}  joined {r['created_at']:%Y-%m-%d}")
            return 0
        uid = a.user_id or await pool.fetchval("select id::text from app_users where lower(email) = lower($1)", a.email)
        if not uid:
            print("no such user", file=sys.stderr)
            return 2
        if not a.question:
            print("give a question", file=sys.stderr)
            return 2
        now = datetime.now(timezone.utc)
        cfg = AIConfig.from_env()
        print(f"now (UTC): {now:%Y-%m-%d %H:%M}   embed model: {cfg.embed_model!r}   AI on: {cfg.enabled}")

        head("1. your teams (active subscriptions)")
        subs = await storage.list_subscriptions(uid)
        teams = {s["team_id"]: s["name"] or "Course" for s in subs}
        for s in subs:
            print(f"  {s['team_id'][:40]:40}  {s['name']!r}  bot removed: {s.get('removed_at') is not None}  muted: {s['muted']}")
        if not teams:
            print("  NONE: with no teams there is nothing to search or answer from.")

        head(f"2. your events (the {EVENT_SCAN} most recently updated)")
        events = await storage.list_events_all_for_user(uid, EVENT_SCAN)
        print(f"  {len(events)} events; by type: {dict(Counter(e['type'] for e in events.values()))}")
        print(f"  upcoming (dated, not over, not cancelled): {sum(1 for e in events.values() if is_upcoming(e, now))}")
        print(f"  needing review (shown as UNCONFIRMED): {sum(1 for e in events.values() if e.get('needs_review') or e.get('confidence') == 'low')}")
        picked = pick_events(events, a.question, now)
        print(f"  picked for THIS question: {len(picked)}")
        for e in picked:
            print("   -", event_line(cards.event_chip(e, set(), detail=True)))

        head("3. embeddings in the database")
        for r in await pool.fetch("select model, dims, kind, count(*) n from embeddings group by 1, 2, 3 order by 1, 3"):
            mark = "   <- the model the chat searches" if r["model"] == cfg.embed_model else ""
            print(f"  {r['model']!r} {r['dims']}d {r['kind']}: {r['n']}{mark}")
        mine = await pool.fetchval(
            """select count(*) from embeddings e where e.model = $1 and e.kind = 'message' and exists (
                 select 1 from conversations c where c.id = e.ref->>'conversation_id' and c.team_id = any($2::text[]))""",
            cfg.embed_model, list(teams)) if teams else 0
        print(f"  message embeddings in YOUR teams under that model: {mine}")

        head(f"4. vector search for the question (top {TOP_K}; hits below {MIN_SCORE} are dropped)")
        services = AIServices.build(cfg, storage) if cfg.enabled else None
        emb = services.embedder if services else None
        if emb is None or not emb.usable:
            print("  no usable embedder: retrieval is OFF (needs GEMINI_API_KEY and 768 dims)")
        elif teams:
            vec = await emb.embed_query(a.question)
            hits = await storage.search_embeddings_teams(vec, cfg.embed_model, list(teams), TOP_K)
            print(f"  {len(hits)} hits" + ("" if hits else ": nothing came back: no embeddings for these teams under this model, or none matched"))
            for h in hits:
                first = (h["snippet"] or "").strip().splitlines()[0][:70] if h["snippet"] else ""
                print(f"   {'keep' if h['score'] >= MIN_SCORE else 'DROP'}  {h['score']:.3f}  {h['kind']:9}  {teams.get(h['team_id'], '?'):14}  {first!r}")

        if a.no_answer or services is None:
            return 0
        head("5. the answer (the same code path as the app; nothing is recorded)")
        svc = ChatService(storage, services.router, emb, cfg, id_secret="diagnose")
        try:
            out = await svc.answer(uid, a.question, a.item_id, [], now)
        except AllProvidersFailed as e:
            print(f"  all model providers failed: {e.error_class}")
            return 1
        print(f"  refused: {out['refused']}\n  answer: {out['answer']}")
        for c in out["citations"]:
            print(f"   [{c['n']}] {c['title']}  ({c['team']})  item {c.get('item_id')}")
        await services.close()
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

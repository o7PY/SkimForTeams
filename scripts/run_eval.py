"""Phase 3 eval runner (FR-310): replay tests/eval/eval_messages.csv through the REAL pipeline and score it (§10.1).

    set -a; . ./.env; set +a                 # needs GROQ_API_KEY, GEMINI_API_KEY, GROQ_MODEL (+ optional GEMINI_MODEL)
    python scripts/run_eval.py               # real providers (free-tier calls with synthetic text)
    python scripts/run_eval.py --dry         # recorded perfect-model replies: tests the code, not the LLM; no network

Runs on an isolated in-memory store (nothing real is read or written). Every run is APPENDED to Bookkeeping/PHASE3_EVAL.md.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "tests" / "eval")]

from skim.ai.config import AIConfig  # noqa: E402
from skim.ai.evaluate import eval_config, format_report, load_rows, replay, score  # noqa: E402
from skim.ai.pipeline import Pipeline  # noqa: E402
from skim.ai.providers.gemini import GeminiProvider  # noqa: E402
from skim.ai.providers.groq import GroqProvider  # noqa: E402
from skim.ai.providers.router import Router  # noqa: E402
from skim.storage.memory import MemoryStorage  # noqa: E402


def git_state() -> str:
    try:
        h = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "src", "tests/eval"], cwd=ROOT, text=True).strip()
        return h + (" (+uncommitted changes in src/ or tests/eval)" if dirty else "")
    except Exception:
        return "unknown"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--csv", default=str(ROOT / "tests" / "eval" / "eval_messages.csv"))
    ap.add_argument("--log", default=str(ROOT / "Bookkeeping" / "PHASE3_EVAL.md"))
    ap.add_argument("--prompt-version", default=None, help="override PROMPT_VERSION for this run")
    ap.add_argument("--note", default="", help="what changed since the last run")
    ap.add_argument("--pause", type=float, default=20.0, help="seconds to wait before retrying failed items (rate limits)")
    ap.add_argument("--interval", type=float, default=4.0, help="seconds between calls to one provider (free-tier pacing)")
    ap.add_argument("--no-log", action="store_true")
    a = ap.parse_args()

    rows = load_rows(Path(a.csv))
    if a.dry:
        from ai_fakes import ScriptedProvider
        from recorded import RECORDED
        provider = ScriptedProvider({r["text"]: RECORDED[int(r["msg_id"])] for r in rows}, model="recorded-perfect")
        provider.name = "recorded"
        base = AIConfig(enabled=True, groq_api_key="x", gemini_api_key="x", groq_model="recorded")
        router = Router([provider])
    else:
        base = AIConfig.from_env()
        if not base.enabled:
            print("AI is not configured: set GROQ_API_KEY, GEMINI_API_KEY and GROQ_MODEL (see the log line above).", file=sys.stderr)
            return 2
        router = Router([GroqProvider(base.groq_api_key, base.groq_model), GeminiProvider(base.gemini_api_key, base.gemini_model)],
                        min_interval=a.interval, max_retry_after=70.0, rate_retries=3)
    from dataclasses import replace
    cfg = eval_config(replace(base, prompt_version=a.prompt_version or base.prompt_version, max_attempts=8))
    store = MemoryStorage()
    pipe = Pipeline(store, router, cfg)

    async def pause():
        if not a.dry and (await store.ai_status(cfg.prompt_version, cfg.max_attempts))["messages_pending"]:
            print(f"  some messages failed (rate limit / outage?); waiting {a.pause:.0f}s before retrying...")
            await asyncio.sleep(a.pause)

    obs = await replay(rows, store, pipe, rounds=1 if a.dry else 6, pause=pause)
    rep = score(rows, obs)
    await router.close()

    models = ("recorded perfect model (--dry)" if a.dry else f"groq={cfg.groq_model}, gemini fallback={cfg.gemini_model}")
    stats = {k: v for k, v in sorted(router.stats.items())}
    head = (f"\n\n## Eval run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC{' (DRY, code-only)' if a.dry else ''}\n"
            f"- git: {git_state()}\n- providers/models: {models}\n- prompt version: {cfg.prompt_version}\n"
            f"- result: {'ALL TARGETS MET' if rep.passed else 'TARGETS NOT MET'}\n- router stats: {stats}\n"
            + (f"- note: {a.note}\n" if a.note else "") + "\n")
    body = format_report(rep) + "\n\n**Per-row failures** (`(info)` = status/venue, not a §10.1 target):\n" + (
        "\n".join(f"- {f}" for f in rep.failures) or "- none")
    print(head + body)
    if not a.no_log:
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        with open(a.log, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("# PHASE3_EVAL.md — every eval run (append only; never overwrite)\n")
            f.write(head + body + "\n")
        print(f"\nappended to {a.log}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Evaluate NVIDIA NIM on its own: smoke-test the connection and structured output, then run the full Phase 3 eval
with NIM as the ONLY provider (so every answer in the scorecard is NIM's; no fallback hides failures).

    set -a; . ./.env; set +a
    export NIM_API_KEY=nvapi-...   NIM_MODEL=meta/llama-3.3-70b-instruct     # any chat model from build.nvidia.com
    python scripts/eval_nim.py [--interval 3] [--note "text"] [--smoke-only]

Appends to Bookkeeping/PHASE3_EVAL.md like run_eval.py. Uses synthetic eval text only; the key is never printed.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from skim.ai.providers.base import ProviderError  # noqa: E402
from skim.ai.providers.nim import NimProvider  # noqa: E402
from skim.ai.providers.router import Router  # noqa: E402
from skim.ai.schemas import MessageExtraction  # noqa: E402

SMOKE = ("Posted Monday 20 Oct 2026 10:00 IST (Asia/Kolkata). Message from staff: "
         "Quiz 2 will be held on Friday 24 Oct at 3 PM in Room 101.")


async def smoke(key: str, model: str) -> bool:
    router = Router([NimProvider(key, model)], rate_retries=1)
    try:
        c = await router.complete(MessageExtraction, "Classify the course message and extract events. Reply with JSON only.", SMOKE)
        items = c.value.items
        print(f"smoke: OK  category={c.value.category} items={len(items)} repairs={router.stats['repairs']} "
              f"tokens={router.stats['tokens']}")
        return True
    except Exception as e:  # AllProvidersFailed carries only an error class, never content
        print(f"smoke: FAILED ({getattr(e, 'error_class', type(e).__name__)}). Check NIM_API_KEY, NIM_MODEL, and the log line above.")
        return False
    finally:
        await router.close()


async def main() -> int:
    key, model = os.environ.get("NIM_API_KEY"), os.environ.get("NIM_MODEL")
    if not key or not model:
        print("Set NIM_API_KEY and NIM_MODEL (e.g. meta/llama-3.3-70b-instruct) in the environment.", file=sys.stderr)
        return 2
    args = sys.argv[1:]
    smoke_only = "--smoke-only" in args
    args = [x for x in args if x != "--smoke-only"]
    if not await smoke(key, model):
        return 1
    if smoke_only:
        return 0
    import run_eval
    note = "NIM only"
    if "--note" in args:
        i = args.index("--note")
        note, args = f"NIM only; {args[i + 1]}", args[:i] + args[i + 2:]
    return await run_eval.main(["--providers", "nim", "--note", note, *args])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

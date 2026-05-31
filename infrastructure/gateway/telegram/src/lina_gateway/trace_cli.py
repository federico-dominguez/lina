"""CLI entry point for persisting reasoning traces from outside the gateway (issue #74).

Usage:
    lina-trace-persist --session-id <id> --thinking-text <text> [--prompt <prompt>]

Requires LINA_DB_URL in the environment (same PostgreSQL connection used by the gateway).
Thin async wrapper around :func:`boot_hook.save_trace`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys

from .boot_hook import save_trace


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persist a DeepSeek reasoning trace to PostgreSQL (issue #74)."
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="Deterministic session ID (e.g. cli-20260531-173000).",
    )
    parser.add_argument(
        "--thinking-text",
        required=True,
        help="Raw <think>…</think> block text for this turn.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="User prompt text (used to derive prompt_hash for deduplication).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    db_url = os.environ.get("LINA_DB_URL")
    if not db_url:
        print("ERROR: LINA_DB_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    prompt_hash = hashlib.sha256(args.prompt.encode()).hexdigest() if args.prompt else None

    try:
        asyncio.run(save_trace(db_url, args.session_id, args.thinking_text, prompt_hash))
    except Exception as exc:
        print(f"ERROR: save_trace failed: {exc}", file=sys.stderr)
        sys.exit(1)

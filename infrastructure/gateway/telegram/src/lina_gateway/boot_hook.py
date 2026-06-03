"""Boot and shutdown hooks for lina-gateway (issue #49).

On startup (``on_boot``):
- Records a ``started`` event in the ``gateway_events`` DB table.
- Checks whether the *previous* run ended gracefully (last event = ``shutdown``)
  or was interrupted/crashed (last event = ``started``).
- Sends an appropriate Telegram message to each trusted chat ID.

On graceful shutdown (``on_shutdown``, called on SIGTERM):
- Records a ``shutdown`` event in the DB.
- Sends a brief "Reiniciándome" warning to trusted chat IDs.

Both functions are **best-effort**: DB or Telegram failures are logged but never
raised — the gateway must continue operating even if the DB is temporarily down.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Nombre del bot — configurable via env var (issue: @s_goose_bot mostraba "LINA")
_BOT_NAME = os.environ.get("GATEWAY_BOT_NAME", "LINA")

# Maximum age of an interrupted 'started' event that we still consider relevant.
# Events older than this are treated as a clean/cold start.
_INTERRUPTION_WINDOW = timedelta(minutes=10)


class _TelegramSender(Protocol):
    """Minimal interface used by the boot hook — matches TelegramClient."""

    async def send_message(self, chat_id: int, text: str) -> int | None: ...  # noqa: E501


# ─── DB helpers ───────────────────────────────────────────────────────────────


async def _record_event(db_url: str, event_type: str) -> None:
    """Insert a lifecycle event into gateway_events. Silently swallows errors."""
    try:
        import asyncpg  # optional dep; only imported when DB is configured

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                "INSERT INTO gateway_events(event_type) VALUES($1)",
                event_type,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("boot_hook: DB write failed (event=%s): %s", event_type, exc)


async def _last_event(db_url: str) -> tuple[str | None, datetime | None]:
    """Return (event_type, created_at) of the most recent gateway_events row."""
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                "SELECT event_type, created_at FROM gateway_events ORDER BY id DESC LIMIT 1"
            )
            if row:
                return row["event_type"], row["created_at"]
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("boot_hook: DB read failed: %s", exc)
    return None, None


# ─── Public API ───────────────────────────────────────────────────────────────


async def on_boot(
    tg: _TelegramSender,
    db_url: str | None,
    chat_ids: list[int],
) -> None:
    """Call once at gateway startup.

    Records a ``started`` event and sends a boot notification to every
    ``chat_id`` in *chat_ids*.  If the previous run was not terminated cleanly
    (crash or OOM) the message includes a recovery note.
    """
    # Record the lifecycle event first, regardless of notification config.
    # This ensures crash detection works even when chat_ids is empty.
    was_interrupted = False
    if db_url:
        last_type, last_ts = await _last_event(db_url)
        if last_type == "started" and last_ts is not None:
            # A 'started' event not followed by 'shutdown' indicates an
            # unexpected restart (crash, OOM, docker kill, etc.).
            age = datetime.now(UTC) - last_ts
            if age < _INTERRUPTION_WINDOW:
                was_interrupted = True
        await _record_event(db_url, "started")

    if not chat_ids:
        return

    if was_interrupted:
        msg = (
            f"⚡ <b>{_BOT_NAME} reiniciada inesperadamente.</b>\n"
            "Retomando — si estabas en medio de algo, volvé a pedírmelo."
        )
    else:
        msg = f"✅ <b>{_BOT_NAME} online.</b>"

    for chat_id in chat_ids:
        try:
            await tg.send_message(chat_id, msg)
        except Exception as exc:
            logger.warning("boot_hook: failed to notify chat %s on boot: %s", chat_id, exc)


async def on_shutdown(
    tg: _TelegramSender,
    db_url: str | None,
    chat_ids: list[int],
) -> None:
    """Call once at gateway shutdown (SIGTERM / graceful stop).

    Records a ``shutdown`` event so the next boot knows the restart was
    intentional, and sends a brief warning to every trusted chat.
    """
    if db_url:
        await _record_event(db_url, "shutdown")

    for chat_id in chat_ids:
        try:
            await tg.send_message(chat_id, "⚠️ Reiniciándome. Vuelvo en ~30s.")
        except Exception as exc:
            logger.warning("boot_hook: failed to notify chat %s on shutdown: %s", chat_id, exc)


# ─── Session message persistence (issue #56) ─────────────────────────────────


async def save_message(
    db_url: str,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """Persist a single conversation turn to ``session_messages``. Best-effort.

    Args:
        db_url:     PostgreSQL connection URL.
        session_id: Gateway deterministic ID (e.g. ``telegram-123456789``).
        role:       One of ``"user"`` | ``"assistant"`` | ``"system"``.
        content:    Plain-text message body (no HTML).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            # turn_number = next value after the current max for this session
            await conn.execute(
                """
                INSERT INTO session_messages (session_id, turn_number, role, content)
                VALUES (
                    $1,
                    COALESCE(
                        (SELECT MAX(turn_number) + 1
                         FROM session_messages
                         WHERE session_id = $1),
                        0
                    ),
                    $2, $3
                )
                """,
                session_id,
                role,
                content,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("boot_hook: save_message failed (session=%s): %s", session_id, exc)


async def get_last_messages(
    db_url: str,
    session_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Return the last *limit* messages for a session, ordered oldest-first.

    Each dict has ``role`` and ``content`` keys.
    Returns an empty list on any error (best-effort).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, turn_number
                    FROM session_messages
                    WHERE session_id = $1
                    ORDER BY turn_number DESC
                    LIMIT $2
                ) sub
                ORDER BY turn_number ASC
                """,
                session_id,
                limit,
            )
            return [{"role": r["role"], "content": r["content"]} for r in rows]
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("boot_hook: get_last_messages failed (session=%s): %s", session_id, exc)
    return []


# ─── Token / cost metering (issue #61, fix #72) ──────────────────────────────

# Real DeepSeek pricing from official billing CSV (verified 2026-05-31).
# Keys: input_cache_miss, input_cache_hit, output — all USD per 1 million tokens.
# Note: goosed's token_state doesn't distinguish cache hits from misses;
# accumulatedCost from goosed is used as-is (best available per-turn estimate).
# For exact monthly totals matching the DeepSeek dashboard, use balance snapshots.
_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {
        "input_cache_miss": 0.14,  # $0.14/1M  (input_cache_miss_tokens)
        "input_cache_hit": 0.0028,  # $0.0028/1M (input_cache_hit_tokens)
        "output": 0.28,  # $0.28/1M  (output_tokens)
    },
    "deepseek-v4-pro": {
        "input_cache_miss": 0.435,  # $0.435/1M (input_cache_miss_tokens)
        "input_cache_hit": 0.003625,  # $0.003625/1M (input_cache_hit_tokens)
        "output": 0.87,  # $0.87/1M  (output_tokens)
    },
    # Legacy aliases
    "deepseek-chat": {
        "input_cache_miss": 0.14,
        "input_cache_hit": 0.0028,
        "output": 0.28,
    },
    "deepseek-reasoner": {
        "input_cache_miss": 0.435,
        "input_cache_hit": 0.003625,
        "output": 0.87,
    },
}
_DEFAULT_MODEL = "deepseek-v4-flash"


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character count (chars / 3, rounded up).

    Empirical ratio for mixed Spanish/English text with DeepSeek tokenizer.
    Error is roughly ±15%.  Returns at least 1 for non-empty text.
    """
    if not text:
        return 0
    return max(1, -(-len(text) // 3))  # ceiling division


def _calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
) -> float:
    """Return estimated cost in USD given model and token counts.

    If cache_hit_tokens is provided, it's separated from cache_miss tokens;
    otherwise all input tokens are treated as cache misses (conservative estimate).
    """
    prices = _PRICING.get(model, _PRICING[_DEFAULT_MODEL])
    cache_miss = max(0, prompt_tokens - cache_hit_tokens)
    cost = (
        cache_miss * prices["input_cache_miss"]
        + cache_hit_tokens * prices["input_cache_hit"]
        + completion_tokens * prices["output"]
    ) / 1_000_000
    return round(cost, 8)


async def save_token_usage(
    db_url: str,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    accumulated_cost_usd: float,
    *,
    model: str = _DEFAULT_MODEL,
) -> None:
    """Persist real token usage for one turn from goosed token_state. Best-effort, never raises.

    Args:
        db_url:               PostgreSQL connection URL.
        session_id:           Gateway deterministic session ID (e.g. ``telegram-123``).
        input_tokens:         Real prompt tokens from goosed token_state.inputTokens.
        output_tokens:        Real completion tokens from goosed token_state.outputTokens.
        accumulated_cost_usd: Monotonic accumulated USD cost for this session from goosed.
                              Per-turn delta is calculated by comparing with the previous row.
        model:                DeepSeek model identifier (default: deepseek-v4-flash).
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            # Calculate per-turn cost delta vs last recorded accumulation for this session
            last_acc = await conn.fetchval(
                "SELECT accumulated_cost_usd FROM token_usage "
                "WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
                session_id,
            )
            last_acc_float = float(last_acc) if last_acc is not None else 0.0
            turn_cost = max(0.0, round(accumulated_cost_usd - last_acc_float, 8))

            await conn.execute(
                """
                INSERT INTO token_usage
                    (session_id, model,
                     prompt_chars, completion_chars,
                     prompt_tokens_est, completion_tokens_est,
                     cost_usd_est, accumulated_cost_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                session_id,
                model,
                0,  # chars no longer tracked (real tokens available)
                0,
                input_tokens,
                output_tokens,
                turn_cost,
                accumulated_cost_usd,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("boot_hook: save_token_usage failed (session=%s): %s", session_id, exc)


# ─── Reasoning trace persistence (issue #62) ─────────────────────────────────


async def save_trace(
    db_url: str,
    session_id: str,
    thinking_text: str,
    prompt_hash: str | None = None,
    *,
    model: str = _DEFAULT_MODEL,
) -> None:
    """Persist the DeepSeek <think>…</think> block for one LINA turn. Best-effort, never raises.

    Args:
        db_url:        PostgreSQL connection URL.
        session_id:    Gateway deterministic session ID (e.g. ``telegram-123456789``).
        thinking_text: Raw text of the accumulated <think> block(s) for this turn.
        prompt_hash:   Optional SHA-256 hex digest of the user prompt for deduplication.
        model:         DeepSeek model identifier (default: deepseek-v4-flash).
    """
    if not thinking_text.strip():
        return
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            # turn_number = next value after the current max for this session
            turn_number = await conn.fetchval(
                "SELECT COALESCE(MAX(turn_number) + 1, 0) "
                "FROM reasoning_traces WHERE session_id = $1",
                session_id,
            )
            await conn.execute(
                """
                INSERT INTO reasoning_traces
                    (session_id, turn_number, thinking_text, prompt_hash, model)
                VALUES ($1, $2, $3, $4, $5)
                """,
                session_id,
                turn_number,
                thinking_text,
                prompt_hash,
                model,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("boot_hook: save_trace failed (session=%s): %s", session_id, exc)


# ─── Balance snapshot tracking (issue #61 real cost) ─────────────────────────


async def record_balance_snapshot(
    db_url: str,
    deepseek_api_key: str,
    source: str = "periodic",
) -> float | None:
    """Fetch the current DeepSeek balance and persist it to balance_snapshots.

    Returns the balance in USD, or None on any error. Best-effort, never raises.

    Strategy: by snapshotting the balance periodically (on startup, hourly, etc.)
    and computing balance deltas, we get exact spend figures matching the
    DeepSeek dashboard — regardless of cache hit/miss pricing nuances.
    """
    try:
        import urllib.request

        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {deepseek_api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json

            data = json.loads(resp.read())

        balance_infos = data.get("balance_infos", [])
        usd_info = next((b for b in balance_infos if b.get("currency") == "USD"), None)
        if not usd_info:
            logger.warning("boot_hook: no USD balance in DeepSeek response")
            return None
        balance = float(usd_info["topped_up_balance"])
        api_key_hint = deepseek_api_key[-4:] if len(deepseek_api_key) >= 4 else "????"

        import asyncpg

        conn = await asyncpg.connect(db_url, timeout=5)
        try:
            await conn.execute(
                "INSERT INTO balance_snapshots (balance_usd, source, api_key_hint) "
                "VALUES ($1, $2, $3)",
                balance,
                source,
                api_key_hint,
            )
        finally:
            await conn.close()

        logger.info("boot_hook: balance snapshot recorded $%.4f (source=%s)", balance, source)
        return balance

    except Exception as exc:
        logger.debug("boot_hook: record_balance_snapshot failed: %s", exc)
        return None


# How many raw recent messages to include alongside the summary.
_RECENT_MESSAGES_LIMIT = 5


async def _get_latest_summary(
    conn: Any,
    session_id: str,
) -> str | None:
    """Return raw_summary for the most recent session_summaries row, or None."""
    row = await conn.fetchrow(
        """
        SELECT raw_summary
        FROM session_summaries
        WHERE session_id = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        session_id,
    )
    return row["raw_summary"] if row else None


async def _get_recent_messages(
    conn: Any,
    session_id: str,
    limit: int,
) -> list[dict[str, str]]:
    """Return the last *limit* messages for *session_id*, oldest-first."""
    rows = await conn.fetch(
        """
        SELECT role, content
        FROM (
            SELECT role, content, turn_number
            FROM session_messages
            WHERE session_id = $1
            ORDER BY turn_number DESC
            LIMIT $2
        ) sub
        ORDER BY turn_number ASC
        """,
        session_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


class SmartContext:
    """Compact context bundle built from structured DB data.

    Attributes:
        summary:  Prose summary of the last session (may be empty string).
        messages: Last N raw messages oldest-first (may be empty list).
        has_data: True if there is anything worth injecting.
    """

    __slots__ = ("summary", "messages")

    def __init__(self, summary: str, messages: list[dict[str, str]]) -> None:
        self.summary = summary
        self.messages = messages

    @property
    def has_data(self) -> bool:
        return bool(self.summary or self.messages)

    def format_warmup_prompt(self) -> str:
        """Return the complete warmup prompt string to send to goosed."""
        agent_name = _BOT_NAME
        parts: list[str] = [
            "[SISTEMA: CONTEXTO_RECUPERADO_AUTOMATICAMENTE]\n"
            f"{agent_name} fue reiniciada. Contexto de la sesión anterior para retomar "
            "sin pedirle al usuario que repita nada:\n"
        ]

        if self.summary:
            parts.append(f"## Resumen de sesión anterior\n{self.summary}\n")

        if self.messages:
            recent = "\n".join(
                f"{'Fede' if m['role'] == 'user' else agent_name}: {m['content']}"
                for m in self.messages
            )
            parts.append(f"## Últimos mensajes\n{recent}\n")

        parts.append(
            "[FIN_CONTEXTO]\n"
            'Confirma que recibiste el contexto respondiendo SOLO con: "✅ Sesión reanudada."'
        )
        return "\n".join(parts)


async def get_smart_context(
    db_url: str,
    session_id: str,
    *,
    recent_limit: int = _RECENT_MESSAGES_LIMIT,
) -> SmartContext:
    """Build a compact context bundle for warmup injection (issue #60).

    Combines:
    1. The structured prose summary from ``session_summaries`` (~200 tokens).
    2. The last *recent_limit* raw messages for continuity (~300 tokens).

    Total budget is roughly 3–5× smaller than the previous 20-message raw dump.
    Returns a ``SmartContext`` with empty fields on any DB error (best-effort).
    """
    try:
        import asyncpg

        # Two separate connections: asyncpg connections are single-operation;
        # running asyncio.gather on the same connection raises
        # "another operation is in progress".
        conn_s = await asyncpg.connect(db_url, timeout=5)
        try:
            summary = await _get_latest_summary(conn_s, session_id)
        finally:
            await conn_s.close()

        conn_m = await asyncpg.connect(db_url, timeout=5)
        try:
            messages = await _get_recent_messages(conn_m, session_id, recent_limit)
        finally:
            await conn_m.close()

        return SmartContext(summary=summary or "", messages=messages)
    except Exception as exc:
        logger.debug("boot_hook: get_smart_context failed (session=%s): %s", session_id, exc)
    return SmartContext(summary="", messages=[])

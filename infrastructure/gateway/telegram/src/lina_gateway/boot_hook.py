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
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

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
            "⚡ <b>LINA reiniciada inesperadamente.</b>\n"
            "Retomando — si estabas en medio de algo, volvé a pedírmelo."
        )
    else:
        msg = "✅ <b>LINA online.</b>"

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

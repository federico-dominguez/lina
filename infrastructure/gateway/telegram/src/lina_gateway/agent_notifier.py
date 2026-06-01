"""Agent notification poller.

Background asyncio task that watches lina-db for sub-agent status transitions
and sends proactive Telegram messages to Federico when an agent completes,
fails, or times out — without him needing to ask.

Design
------
- Polls ``agent_sessions`` every ``poll_interval`` seconds.
- Tracks a ``_last_seen`` watermark (datetime) to avoid double-notifying.
- Only notifies on terminal states: completed / failed / killed / timeout.
- All DB errors are swallowed silently (best-effort — gateway must not crash).
- Uses asyncpg (already a gateway dependency).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .telegram_client import TelegramClient

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "killed", "timeout")

_STATUS_ICON: dict[str, str] = {
    "completed": "✅",
    "failed": "❌",
    "killed": "⛔",
    "timeout": "⏰",
}
_STATUS_LABEL: dict[str, str] = {
    "completed": "completó",
    "failed": "falló",
    "killed": "fue detenido",
    "timeout": "agotó el tiempo",
}


class AgentNotifier:
    """Polls lina-db for agent status changes and pushes Telegram notifications.

    Instantiate once, then ``await notifier.run()`` as a background task.
    """

    def __init__(
        self,
        db_url: str,
        tg: TelegramClient,
        chat_ids: list[int],
        poll_interval: float = 10.0,
    ) -> None:
        self._db_url = db_url
        self._tg = tg
        self._chat_ids = chat_ids
        self._poll_interval = poll_interval
        # Watermark: only notify rows with updated_at strictly after this value.
        # Initialised to "now" so we don't replay old completed agents on startup.
        self._last_seen: datetime = datetime.now(UTC)

    async def run(self) -> None:
        """Run forever, polling DB for agent transitions. Cancellable."""
        logger.info(
            "AgentNotifier started (interval=%.0fs, chats=%s)",
            self._poll_interval,
            self._chat_ids,
        )
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("AgentNotifier._check error (non-fatal): %s", exc)

    async def _check(self) -> None:
        """Query DB for new terminal transitions and push notifications."""
        import asyncpg  # optional dep; only when DB is configured

        try:
            conn = await asyncpg.connect(self._db_url, timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AgentNotifier: DB connect failed: %s", exc)
            return

        try:
            rows = await conn.fetch(
                """
                SELECT id, role, goal, status, result_summary, updated_at
                FROM agent_sessions
                WHERE updated_at > $1
                  AND status = ANY($2::text[])
                ORDER BY updated_at ASC
                """,
                self._last_seen,
                list(_TERMINAL_STATUSES),
            )
        finally:
            await conn.close()

        if not rows:
            return

        for row in rows:
            msg = _format_notification(dict(row))
            for chat_id in self._chat_ids:
                try:
                    await self._tg.send_message(chat_id, msg)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("AgentNotifier: send to chat %s failed: %s", chat_id, exc)

        # Advance watermark — don't re-notify the same rows on the next tick.
        self._last_seen = max(row["updated_at"] for row in rows)


def _format_notification(row: dict) -> str:
    """Format a terminal status transition as a Telegram HTML message."""
    status = row["status"]
    role = row["role"]
    short_id = row["id"][:8]
    goal = row["goal"]
    goal_short = goal[:80] + ("…" if len(goal) > 80 else "")
    result = (row.get("result_summary") or "").strip()

    icon = _STATUS_ICON.get(status, "ℹ️")
    label = _STATUS_LABEL.get(status, status)

    lines = [
        f"{icon} <b>Agente {role} {label}</b>",
        f"ID: <code>{short_id}…</code>",
        f"Tarea: {goal_short}",
    ]
    if result:
        result_trimmed = result[:300] + ("…" if len(result) > 300 else "")
        lines.append(f"Resultado: {result_trimmed}")

    return "\n".join(lines)

"""
TelegramTestClient — Telethon-based user client for E2E testing of Lina.

Connects as a Telegram *user* (not bot) so it can receive messages from the
Lina bot and observe edits in real time.

Required env vars:
    TELEGRAM_TEST_API_ID      — from my.telegram.org
    TELEGRAM_TEST_API_HASH    — from my.telegram.org
    TELEGRAM_TEST_PHONE       — phone number used to log in (e.g. +59812345678)
    LINA_BOT_USERNAME         — bot username to test (e.g. @LinaTestBot)

Optional:
    LINA_E2E_SESSION_DIR      — directory to persist the .session file
                                (default: tests/e2e/telegram/.sessions)
    LINA_E2E_COLLECT_TIMEOUT  — seconds to wait for bot reply before giving up
                                (default: 90)
    LINA_E2E_STABLE_WINDOW    — seconds of inactivity to consider reply done
                                (default: 3.0)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from telethon import TelegramClient, events  # type: ignore[import]
from telethon.tl.types import Message  # type: ignore[import]

from .capture import ResponseCapture

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_DIR = Path(__file__).parent / ".sessions"
_DEFAULT_COLLECT_TIMEOUT = 90.0
_DEFAULT_STABLE_WINDOW = 3.0


def _env_require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(
            f"Required env var {name!r} not set. "
            "See tests/e2e/telegram/README.md for setup instructions."
        )
    return v


class TelegramTestClient:
    """Wraps a Telethon client focused on E2E testing of the Lina bot."""

    def __init__(
        self,
        *,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        phone: Optional[str] = None,
        bot_username: Optional[str] = None,
        session_dir: Optional[Path] = None,
        collect_timeout: float = _DEFAULT_COLLECT_TIMEOUT,
        stable_window: float = _DEFAULT_STABLE_WINDOW,
    ) -> None:
        self._api_id = api_id or int(_env_require("TELEGRAM_TEST_API_ID"))
        self._api_hash = api_hash or _env_require("TELEGRAM_TEST_API_HASH")
        self._phone = phone or _env_require("TELEGRAM_TEST_PHONE")
        self._bot_username = bot_username or _env_require("LINA_BOT_USERNAME")
        self._session_dir = session_dir or Path(
            os.environ.get("LINA_E2E_SESSION_DIR", str(_DEFAULT_SESSION_DIR))
        )
        self._collect_timeout = float(
            os.environ.get("LINA_E2E_COLLECT_TIMEOUT", str(collect_timeout))
        )
        self._stable_window = float(
            os.environ.get("LINA_E2E_STABLE_WINDOW", str(stable_window))
        )

        self._session_dir.mkdir(parents=True, exist_ok=True)
        session_path = str(self._session_dir / "lina_e2e")
        self._client = TelegramClient(session_path, self._api_id, self._api_hash)
        self._bot_id: Optional[int] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect and authenticate (interactive on first run)."""
        await self._client.start(phone=self._phone)
        bot_entity = await self._client.get_entity(self._bot_username)
        self._bot_id = bot_entity.id
        logger.info("Connected as user; bot_id=%s", self._bot_id)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def __aenter__(self) -> "TelegramTestClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ── Core probe ────────────────────────────────────────────────────────────

    async def send_prompt(
        self,
        text: str,
        *,
        timeout: Optional[float] = None,
        stable_window: Optional[float] = None,
    ) -> ResponseCapture:
        """Send *text* to the bot and collect all response messages/edits.

        Returns a :class:`ResponseCapture` once the bot is silent for
        *stable_window* seconds or *timeout* is reached.
        """
        timeout = timeout or self._collect_timeout
        stable = stable_window or self._stable_window

        capture = ResponseCapture()

        # Event queues: (kind, msg_id, text, ts)
        _KINDS = ("new", "edit")
        queue: asyncio.Queue[tuple[str, int, str, float]] = asyncio.Queue()

        def _msg_from_bot(event: events.NewMessage.Event) -> bool:
            return bool(
                event.message
                and event.message.sender_id == self._bot_id
            )

        def _edit_from_bot(event: events.MessageEdited.Event) -> bool:
            return bool(
                event.message
                and event.message.sender_id == self._bot_id
            )

        @self._client.on(events.NewMessage(from_users=self._bot_id))
        async def _on_new(event: events.NewMessage.Event) -> None:
            msg: Message = event.message
            text_content = msg.text or ""
            queue.put_nowait(("new", msg.id, text_content, time.monotonic()))

        @self._client.on(events.MessageEdited(from_users=self._bot_id))
        async def _on_edit(event: events.MessageEdited.Event) -> None:
            msg: Message = event.message
            text_content = msg.text or ""
            queue.put_nowait(("edit", msg.id, text_content, time.monotonic()))

        try:
            prompt_ts = time.monotonic()
            capture = ResponseCapture(prompt_sent_at=prompt_ts)

            await self._client.send_message(self._bot_username, text)
            logger.debug("Prompt sent at %.3f", prompt_ts)

            deadline = prompt_ts + timeout
            last_activity = prompt_ts

            while True:
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    logger.warning("collect_response: timeout after %.0fs", timeout)
                    break

                idle_for = now - last_activity
                if capture.messages and idle_for >= stable:
                    logger.debug(
                        "collect_response: stable window reached after %.1fs idle", idle_for
                    )
                    break

                wait = min(stable, remaining, stable - idle_for + 0.05)
                try:
                    kind, msg_id, text_content, ts = await asyncio.wait_for(
                        queue.get(), timeout=max(0.05, wait)
                    )
                    last_activity = ts
                    if kind == "new":
                        capture.on_message(msg_id, text_content, ts)
                        logger.debug("new msg_id=%s chars=%s", msg_id, len(text_content))
                    else:
                        capture.on_edit(msg_id, text_content, ts)
                        logger.debug("edit msg_id=%s chars=%s", msg_id, len(text_content))
                except asyncio.TimeoutError:
                    continue

        finally:
            capture.seal()
            # Remove the handlers we registered
            self._client.remove_event_handler(_on_new)
            self._client.remove_event_handler(_on_edit)

        logger.info("capture: %s", capture.summary())
        return capture

    async def send_command(self, command: str) -> None:
        """Send a command to the bot without capturing a full response."""
        await self._client.send_message(self._bot_username, command)

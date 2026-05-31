"""
Main bot loop: Telegram long-poll → goosed SSE → Telegram edits.

Mirrors the logic in handler.rs:
  - Auto-session-id: one persistent session per chat_id
  - /stop: cancel in-flight request
  - Typewriter display via StreamingBubble + Pacer
  - Goosed-down detection with user notification
  - Long messages (>4096 chars) are split across multiple Telegram messages
    without truncation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

import httpx

from .boot_hook import get_smart_context, save_message, save_token_usage
from .config import Config
from .formatter import (
    format_tool_status,
    format_with_thinking,
    markdown_to_telegram_html,
    split_message,
)
from .goose_client import EventType, GoosedClient
from .pacer import StreamingBubble
from .telegram_client import MAX_VOICE_FILE_SIZE, TelegramClient, TelegramMessage, voice_prompt

logger = logging.getLogger(__name__)

_STOP_COMMANDS = {"/stop", "stop", "/Stop", "Stop", "/STOP", "STOP"}
_TG_CHAR_LIMIT = 4096
_OVERFLOW_MARGIN = 200  # start splitting when content approaches the limit

# httpx exceptions that indicate a goosed restart/connection drop (infrastructure,
# NOT agent errors). These should never be shown verbatim to the user.
_GOOSED_TRANSPORT_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
)
_GOOSED_RESTART_TIMEOUT = 90.0  # seconds to wait for goosed to come back up


class Bot:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._tg = TelegramClient(cfg.bot_token, poll_timeout=cfg.poll_timeout)
        self._goosed = GoosedClient(
            base_url=cfg.goosed_url,
            secret=cfg.goosed_secret,
            connect_timeout=cfg.goosed_connect_timeout,
            read_timeout=cfg.goosed_read_timeout,
        )
        # chat_id → session_id (persistent per chat)
        self._sessions: dict[int, str] = {}
        # chat_id → active cancel event (one reply at a time per chat)
        self._cancels: dict[int, asyncio.Event] = defaultdict(asyncio.Event)
        # chat_id → True if currently processing
        self._busy: dict[int, bool] = {}
        # chat_ids for which session context has been injected this process lifetime
        self._sessions_initialized: set[int] = set()

    @property
    def tg(self) -> TelegramClient:
        """Expose the Telegram client for use outside the bot loop (e.g. boot hook)."""
        return self._tg

    def _session_id(self, chat_id: int) -> str:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = f"telegram-{chat_id}"
        return self._sessions[chat_id]

    async def _handle(self, msg: TelegramMessage) -> None:
        chat_id = msg.chat.id
        text = msg.text or ""

        # ── /stop ──────────────────────────────────────────────────────
        if text.strip() in _STOP_COMMANDS:
            if self._busy.get(chat_id):
                self._cancels[chat_id].set()
                await self._tg.send_message(chat_id, "⛔ Deteniendo.")
            else:
                await self._tg.send_message(chat_id, "ℹ️ No hay ninguna tarea en curso.")
            return

        # ── voice note ─────────────────────────────────────────────────
        if msg.voice:
            if msg.voice.file_size and msg.voice.file_size > MAX_VOICE_FILE_SIZE:
                await self._tg.send_message(
                    chat_id, "⚠️ El archivo de voz excede el límite de 20 MB."
                )
                return
            try:
                data = await self._tg.download_file(msg.voice.file_id)
                path = await self._tg.save_voice_file(data, msg.voice.mime_type)
                text = voice_prompt(path, msg.voice.duration, msg.voice.mime_type)
            except Exception as exc:
                logger.error("Failed to download voice file: %s", exc)
                await self._tg.send_message(chat_id, "⚠️ No pude descargar la nota de voz.")
                return

        if not text.strip():
            return

        # ── Goosed health check ─────────────────────────────────────────
        if not await self._goosed.is_alive():
            # goosed may be mid-restart — wait briefly before giving up
            status_id = await self._tg.send_message(
                chat_id, "⏳ LINA se está reiniciando, un momento..."
            )
            recovered = await self._wait_for_goosed()
            if not recovered:
                await self._tg.edit_message(
                    chat_id,
                    status_id,
                    "⚠️ LINA no está disponible. Intentá de nuevo en unos segundos.",
                )
                return
            await self._tg.edit_message(chat_id, status_id, "✅ LINA de vuelta.")

        # ── Typing indicator ────────────────────────────────────────────
        await self._tg.send_chat_action(chat_id, "typing")
        await self._tg.set_reaction(chat_id, msg.message_id, "⚡")

        # ── Reset cancel token ──────────────────────────────────────────
        cancel_event = asyncio.Event()
        self._cancels[chat_id] = cancel_event
        self._busy[chat_id] = True

        try:
            await self._reply(chat_id, msg.message_id, text, cancel_event)
        finally:
            self._busy[chat_id] = False
            # Clear reaction on original message
            await self._tg.set_reaction(chat_id, msg.message_id, "")

    async def _wait_for_goosed(self, *, timeout: float = _GOOSED_RESTART_TIMEOUT) -> bool:
        """Poll until goosed responds to /status or timeout expires. Returns True if recovered."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._goosed.is_alive():
                return True
            await asyncio.sleep(3.0)
        return False

    async def _reply(
        self,
        chat_id: int,
        user_msg_id: int,
        text: str,
        cancel_event: asyncio.Event,
        *,
        _retry: bool = False,
    ) -> None:
        session_id = self._session_id(chat_id)
        reply_start = time.monotonic()
        first_send_ts: float | None = None  # timestamp of the first message sent to the user

        # Ensure the session exists in goosed (creates it if needed)
        session_is_new = False
        try:
            session_id, session_is_new = await self._goosed.ensure_session(session_id)
            self._sessions[chat_id] = session_id
        except Exception as exc:
            logger.warning("Could not ensure session for chat %s: %s", chat_id, exc)

        # First message to this chat in this process lifetime: inject previous context
        if chat_id not in self._sessions_initialized:
            self._sessions_initialized.add(chat_id)
            if self._cfg.lina_db_url and session_is_new:
                await self._maybe_inject_context(chat_id, session_id, cancel_event)

        # Accumulators for the current turn
        thinking_acc = ""
        body_acc = ""

        # Two SEPARATE Telegram messages:
        #   thinking_bubble → shows only the 💭 reasoning block, live-updated
        #   body_bubble     → shows the final response text, created after thinking seals
        thinking_bubble_msg_id: int | None = None
        thinking_bubble: StreamingBubble | None = None
        body_bubble_msg_id: int | None = None
        body_bubble: StreamingBubble | None = None

        # Overflow tracking for body: once the message exceeds 4096 chars,
        # we seal the current bubble and send continuation messages manually.
        body_overflowed: bool = False
        # Character position in body_acc that has already been delivered
        # (used to send only the delta after overflow).
        body_delivered_offset: int = 0

        # Tool tracking: tool_name → (tool_name, args, msg_id)
        active_tools: dict[str, tuple[str, str, int]] = {}

        async def _edit_thinking_bubble(body_unused: str, thinking: str, sealed: bool) -> None:
            nonlocal thinking_bubble_msg_id
            if thinking_bubble_msg_id is None:
                return
            html = format_with_thinking(thinking, "", sealed)
            if not html.strip():
                return
            await self._tg.edit_message(chat_id, thinking_bubble_msg_id, html)

        async def _edit_body_bubble(body: str, thinking_unused: str, sealed: bool) -> None:
            nonlocal body_bubble_msg_id, body_bubble, body_overflowed, body_delivered_offset
            if body_bubble_msg_id is None:
                return

            html = markdown_to_telegram_html(body)
            if not html.strip():
                return

            # ── After overflow, send only the delta as a new message ─────
            if body_overflowed:
                new_text = body[body_delivered_offset:]
                if not new_text:
                    return
                delta_html = markdown_to_telegram_html(new_text)
                if delta_html.strip():
                    new_id = await self._tg.send_message(chat_id, delta_html)
                    if new_id is not None:
                        body_bubble_msg_id = new_id
                body_delivered_offset = len(body)
                return

            # ── Check if content fits in one Telegram message ───────────
            chunks = split_message(html, max_len=_TG_CHAR_LIMIT - _OVERFLOW_MARGIN)
            if len(chunks) <= 1:
                await self._tg.edit_message(chat_id, body_bubble_msg_id, html)
                return

            # ── Overflow detected: seal current bubble and send rest ────
            # Edit current message with first chunk (no truncation indicator)
            await self._tg.edit_message(chat_id, body_bubble_msg_id, chunks[0])

            body_overflowed = True
            body_delivered_offset = len(body)

            # Send remaining chunks as independent messages
            for chunk in chunks[1:]:
                new_id = await self._tg.send_message(chat_id, chunk)
                if new_id is not None:
                    body_bubble_msg_id = new_id

        async def _seal_all() -> None:
            nonlocal thinking_bubble, body_bubble
            if thinking_bubble:
                await thinking_bubble.seal()
                thinking_bubble = None
            if body_bubble:
                await body_bubble.seal()
                body_bubble = None

        try:
            async for event in self._goosed.reply_stream(session_id, text):
                if cancel_event.is_set():
                    break

                if event.event_type == EventType.ERROR:
                    await _seal_all()
                    await self._tg.send_message(chat_id, f"⚠️ Error: <code>{event.error}</code>")
                    return

                if event.event_type == EventType.FINISH:
                    break

                if event.event_type != EventType.MESSAGE:
                    continue

                # Process content items
                for item in event.contents:
                    if item.content_type == "thinking":
                        thinking_acc += item.thinking
                        if thinking_bubble_msg_id is None:
                            # Open the thinking bubble (its own Telegram message)
                            html = format_with_thinking(thinking_acc, "", False)
                            thinking_bubble_msg_id = await self._tg.send_message(chat_id, html)
                            if first_send_ts is None:
                                first_send_ts = time.monotonic()
                            thinking_bubble = StreamingBubble(
                                tick=self._cfg.pacer_tick,
                                edit_fn=_edit_thinking_bubble,
                            )
                            thinking_bubble.start()
                            thinking_bubble.update(thinking=thinking_acc, body="")
                        else:
                            if thinking_bubble:
                                thinking_bubble.update(thinking=thinking_acc, body="")

                    elif item.content_type == "text":
                        body_acc += item.text
                        if body_bubble_msg_id is None:
                            # Seal thinking bubble first (marks it as collapsed/expandable)
                            if thinking_bubble:
                                await thinking_bubble.seal()
                                thinking_bubble = None
                            # Open a NEW message for the body text
                            html = markdown_to_telegram_html(body_acc)
                            if not html.strip():
                                html = "…"  # placeholder; overwritten on next tick
                            body_bubble_msg_id = await self._tg.send_message(chat_id, html)
                            if first_send_ts is None:
                                first_send_ts = time.monotonic()
                            body_bubble = StreamingBubble(
                                tick=self._cfg.pacer_tick,
                                edit_fn=_edit_body_bubble,
                            )
                            body_bubble.start()
                            body_bubble.update(thinking="", body=body_acc)
                        else:
                            if body_bubble:
                                body_bubble.update(thinking="", body=body_acc)

                    elif item.content_type == "tool_request":
                        # Seal all active bubbles before showing tool status
                        await _seal_all()

                        html = format_tool_status(
                            item.tool_name, item.args_preview, False, None, ""
                        )
                        tool_msg_id = await self._tg.send_message(chat_id, html)
                        if tool_msg_id is not None:
                            active_tools[item.tool_name] = (
                                item.tool_name,
                                item.args_preview,
                                tool_msg_id,
                            )

                        # Reset accumulators for the next reasoning/response turn
                        thinking_acc = ""
                        body_acc = ""
                        thinking_bubble_msg_id = None
                        body_bubble_msg_id = None
                        body_overflowed = False
                        body_delivered_offset = 0

                    elif item.content_type == "tool_response":
                        # Update the matching tool status card
                        tool_entry = active_tools.pop(item.tool_name, None)
                        if tool_entry:
                            tool_name, args_preview, tmsg_id = tool_entry
                            html = format_tool_status(
                                tool_name, args_preview, True, item.success, item.result_preview
                            )
                            await self._tg.edit_message(chat_id, tmsg_id, html)

        except asyncio.CancelledError:
            pass
        except _GOOSED_TRANSPORT_ERRORS as exc:
            # Connection dropped — goosed is restarting (model change, watchdog, OOM).
            # Never surface raw transport errors to the user.
            logger.warning("goosed connection lost (likely restart): %s", exc)
            await _seal_all()
            if _retry:
                # Already retried once — give up gracefully
                await self._tg.send_message(chat_id, "⚠️ LINA no está disponible. Intentá de nuevo.")
                return
            status_id = await self._tg.send_message(
                chat_id, "⏳ LINA se está reiniciando, un momento..."
            )
            recovered = await self._wait_for_goosed()
            if not recovered:
                await self._tg.edit_message(
                    chat_id,
                    status_id,
                    "⚠️ LINA no está disponible. Intentá de nuevo.",
                )
                return
            await self._tg.edit_message(chat_id, status_id, "✅ LINA de vuelta. Reprocesando...")
            # Invalidate cached session — force ensure_session to create/resume fresh
            self._sessions.pop(chat_id, None)
            await asyncio.sleep(1.0)  # let goosed settle
            await self._reply(chat_id, user_msg_id, text, cancel_event, _retry=True)
            return
        except Exception as exc:
            logger.exception("Unexpected error during reply: %s", exc)
            await _seal_all()
            # Expose only the error type, not the full message (may contain internals)
            await self._tg.send_message(
                chat_id, f"⚠️ Error inesperado (<code>{type(exc).__name__}</code>). Revisá los logs."
            )
            return

        # Final seal of whichever bubble is still active
        await _seal_all()

        total_s = time.monotonic() - reply_start
        ttft_s = (first_send_ts - reply_start) if first_send_ts else total_s
        logger.info(
            "reply: chat=%s ttft=%.2fs total=%.2fs",
            chat_id,
            ttft_s,
            total_s,
        )

        # Belt-and-suspenders: body arrived but no bubble was created somehow
        if body_acc and body_bubble_msg_id is None and thinking_bubble_msg_id is None:
            await self._tg.send_message(chat_id, markdown_to_telegram_html(body_acc))

        # Persist this turn for session recovery across restarts (best-effort)
        if self._cfg.lina_db_url and text.strip() and body_acc.strip():
            asyncio.create_task(
                self._persist_turn(session_id, text, body_acc),
                name=f"persist-turn-{chat_id}",
            )

    # ─── Session persistence helpers ─────────────────────────────────────────

    async def _persist_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        """Save a user+assistant turn to PostgreSQL. Silently swallows errors."""
        db_url = self._cfg.lina_db_url
        if not db_url:
            return
        try:
            await save_message(db_url, session_id, "user", user_text)
            await save_message(db_url, session_id, "assistant", assistant_text)
            await save_token_usage(db_url, session_id, user_text, assistant_text)
        except Exception as exc:
            logger.debug("_persist_turn failed for session %s: %s", session_id, exc)

    async def _maybe_inject_context(
        self,
        chat_id: int,
        session_id: str,
        cancel_event: asyncio.Event,
    ) -> None:
        """Inject a compact smart context bundle into a freshly-created goosed session.

        Uses ``get_smart_context`` (issue #60) which combines:
        - A structured prose summary from ``session_summaries`` (~200 tokens)
        - The last 5 raw messages for immediate continuity (~300 tokens)

        This replaces the previous raw 20-message dump (~4 000 tokens) with a
        ≤700-token warmup that is more focused and signal-dense.

        Shows a brief status message to the user while loading, then edits it
        once context is ready (or swallows errors silently if DB/goosed is down).
        Only called when ``ensure_session`` confirmed this is a *new* session.
        """
        db_url = self._cfg.lina_db_url
        if not db_url:
            return

        ctx = await get_smart_context(db_url, session_id)
        if not ctx.has_data:
            return  # first session ever — nothing to inject

        warmup_prompt = ctx.format_warmup_prompt()

        status_id = await self._tg.send_message(
            chat_id, "📚 Recuperando contexto de sesión anterior..."
        )

        try:
            async with asyncio.timeout(20.0):
                async for event in self._goosed.reply_stream(session_id, warmup_prompt):
                    if cancel_event.is_set():
                        break
                    # Consume events but don't show them to the user
        except TimeoutError:
            logger.warning("_maybe_inject_context: warmup timed out for session %s", session_id)
        except Exception as exc:
            logger.warning(
                "_maybe_inject_context: warmup failed for session %s: %s", session_id, exc
            )

        try:
            await self._tg.edit_message(
                chat_id, status_id, "📚 Contexto de sesión anterior recuperado."
            )
        except Exception:
            pass  # status edit is best-effort

    async def run_once(self, offset: int | None) -> int | None:
        """Poll once. Returns the new offset."""
        updates = await self._tg.get_updates(offset)
        for update in updates:
            offset = update.update_id + 1
            if update.message:
                asyncio.create_task(self._handle(update.message))
        return offset

    async def run(self) -> None:
        """Main loop: poll forever."""
        logger.info("lina-gateway starting (goosed=%s)", self._cfg.goosed_url)
        offset: int | None = None
        retry_delay = 1.0
        while True:
            try:
                offset = await self.run_once(offset)
                retry_delay = 1.0
            except Exception as exc:
                logger.error("Poll error (retry in %.0fs): %s", retry_delay, exc)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

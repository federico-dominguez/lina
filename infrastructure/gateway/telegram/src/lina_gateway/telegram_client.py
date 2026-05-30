"""Low-level Telegram Bot API client (long-poll, sendMessage, editMessage, etc.)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

# Minimum seconds between successive edits of the same message.
# Safety net against 429 flood-waits during rapid streaming updates.
_MIN_EDIT_INTERVAL_S = 0.5

# Indicator appended to the first chunk when a message must be truncated.
_EDIT_TRUNCATION_INDICATOR = "\n\n<i>… [respuesta truncada]</i>"

# Maximum retries on Telegram 429 Too Many Requests.
_MAX_429_RETRIES = 3

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_VOICE_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


@dataclass
class TelegramUser:
    first_name: str
    last_name: str | None
    username: str | None


@dataclass
class TelegramChat:
    id: int
    chat_type: str


@dataclass
class TelegramVoice:
    file_id: str
    file_size: int | None
    duration: int | None
    mime_type: str | None


@dataclass
class TelegramMessage:
    message_id: int
    chat: TelegramChat
    from_user: TelegramUser | None
    text: str | None
    voice: TelegramVoice | None


@dataclass
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None


class TelegramClient:
    """Minimal async Telegram Bot API client."""

    def __init__(self, bot_token: str, poll_timeout: int = 30) -> None:
        self._token = bot_token
        self._poll_timeout = poll_timeout
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=poll_timeout + 15.0, write=30.0, pool=5.0),
            http2=False,
        )
        # Tracks last edit timestamp per (chat_id, message_id) for throttling.
        self._last_edit_at: dict[tuple[int, int], float] = {}

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self._token}/{method}"

    async def get_updates(self, offset: int | None) -> list[TelegramUpdate]:
        params: dict[str, Any] = {
            "timeout": self._poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            params["offset"] = offset

        resp = await self._http.post(self._url("getUpdates"), json=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getUpdates error: {data.get('description')}")
        return [_parse_update(u) for u in data.get("result", [])]

    async def send_message(self, chat_id: int, html: str) -> int | None:
        """Send *html* to *chat_id*, splitting if necessary.  Returns last message_id."""
        from .formatter import split_message, strip_html_tags

        last_id: int | None = None
        chunks = split_message(html)
        for chunk in chunks:
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            resp: httpx.Response | None = None
            for attempt in range(_MAX_429_RETRIES):
                resp = await self._http.post(self._url("sendMessage"), json=payload)
                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                    logger.warning(
                        "sendMessage 429 chat=%s retry_after=%ss attempt=%s",
                        chat_id, retry_after, attempt + 1,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                break
            if resp is None:
                logger.error("sendMessage: no response after retries chat=%s", chat_id)
                return last_id
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                last_id = data["result"].get("message_id")
                logger.debug("sendMessage ok chat=%s chars=%s", chat_id, len(chunk))
            else:
                # HTML rejected — fallback to plain text
                logger.warning(
                    "sendMessage HTML rejected chat=%s err=%s, falling back to plain",
                    chat_id,
                    data.get("description"),
                )
                plain_chunks = split_message(strip_html_tags(html))
                for plain in plain_chunks:
                    r2 = await self._http.post(
                        self._url("sendMessage"),
                        json={"chat_id": chat_id, "text": plain},
                    )
                    r2.raise_for_status()
                    d2 = r2.json()
                    if d2.get("ok"):
                        last_id = d2["result"].get("message_id")
                return last_id
        return last_id

    async def edit_message(self, chat_id: int, message_id: int, html: str) -> None:
        """Edit an existing message.  Ignores "not modified" errors silently.

        Handles 429 with Retry-After back-off, and throttles successive edits
        of the same message to avoid triggering Telegram flood limits.  When
        the formatted HTML exceeds Telegram's 4096-character limit the first
        chunk is sent and a truncation indicator is appended.
        """
        from .formatter import split_message, strip_html_tags

        # ── Per-message edit throttle ─────────────────────────────────────
        key = (chat_id, message_id)
        last = self._last_edit_at.get(key, 0.0)
        gap = asyncio.get_running_loop().time() - last
        if gap < _MIN_EDIT_INTERVAL_S:
            await asyncio.sleep(_MIN_EDIT_INTERVAL_S - gap)

        # ── Truncate with indicator when message is too long ──────────────
        chunks = split_message(html)
        if len(chunks) > 1:
            # Make room for the truncation indicator inside the 4096-char limit
            indicator = _EDIT_TRUNCATION_INDICATOR
            room = len(indicator)
            short_chunks = split_message(html, max_len=4096 - room)
            text = short_chunks[0] + indicator
        else:
            text = chunks[0] if chunks else html

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }

        for attempt in range(_MAX_429_RETRIES):
            try:
                resp = await self._http.post(self._url("editMessageText"), json=payload)
            except Exception as exc:
                logger.warning("editMessageText network error: %s", exc)
                return

            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                logger.warning(
                    "editMessageText 429 chat=%s msg=%s retry_after=%ss attempt=%s",
                    chat_id, message_id, retry_after, attempt + 1,
                )
                await asyncio.sleep(retry_after)
                continue

            if not resp.is_success:
                body = resp.text
                if "message is not modified" in body:
                    break
                if resp.status_code == 400:
                    # HTML probably malformed (streaming partial tag) — retry as plain
                    plain = strip_html_tags(text)
                    r2 = await self._http.post(
                        self._url("editMessageText"),
                        json={"chat_id": chat_id, "message_id": message_id, "text": plain},
                    )
                    if not r2.is_success and "message is not modified" not in r2.text:
                        logger.warning(
                            "editMessageText plain fallback failed chat=%s msg=%s err=%s",
                            chat_id, message_id, r2.text[:200],
                        )
                    break
                logger.warning(
                    "editMessageText failed chat=%s msg=%s status=%s err=%s",
                    chat_id, message_id, resp.status_code, body[:200],
                )
            break  # success or non-retryable error

        self._last_edit_at[key] = asyncio.get_running_loop().time()

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self._http.post(
                self._url("sendChatAction"),
                json={"chat_id": chat_id, "action": action},
            )
        except Exception as exc:
            logger.debug("sendChatAction error: %s", exc)

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        reaction: list[Any] = [] if not emoji else [{"type": "emoji", "emoji": emoji}]
        try:
            await self._http.post(
                self._url("setMessageReaction"),
                json={"chat_id": chat_id, "message_id": message_id, "reaction": reaction},
            )
        except Exception as exc:
            logger.debug("setMessageReaction error: %s", exc)

    async def get_file_path(self, file_id: str) -> str:
        resp = await self._http.post(self._url("getFile"), json={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile error: {data.get('description')}")
        return data["result"]["file_path"]

    async def download_file(self, file_id: str) -> bytes:
        file_path = await self.get_file_path(file_id)
        url = f"{TELEGRAM_API_BASE}/file/bot{self._token}/{file_path}"
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.content

    async def save_voice_file(self, data: bytes, mime_type: str | None) -> str:
        """Save voice bytes to a temp file and return the path."""
        ext = _ext_from_mime(mime_type)
        voice_dir = os.path.join(tempfile.gettempdir(), "lina_voice")
        os.makedirs(voice_dir, mode=0o700, exist_ok=True)
        filename = f"voice_{uuid.uuid4()}.{ext}"
        path = os.path.join(voice_dir, filename)
        with open(path, "wb") as f:
            os.chmod(path, 0o600)
            f.write(data)
        return path

    async def close(self) -> None:
        await self._http.aclose()


# ─── Parsing helpers ─────────────────────────────────────────────────────────


def _parse_update(raw: dict[str, Any]) -> TelegramUpdate:
    msg = raw.get("message")
    return TelegramUpdate(
        update_id=raw["update_id"],
        message=_parse_message(msg) if msg else None,
    )


def _parse_message(raw: dict[str, Any]) -> TelegramMessage:
    chat = raw["chat"]
    from_raw = raw.get("from")
    voice_raw = raw.get("voice") or raw.get("audio")

    from_user = None
    if from_raw:
        from_user = TelegramUser(
            first_name=from_raw.get("first_name", ""),
            last_name=from_raw.get("last_name"),
            username=from_raw.get("username"),
        )

    voice = None
    if voice_raw:
        voice = TelegramVoice(
            file_id=voice_raw["file_id"],
            file_size=voice_raw.get("file_size"),
            duration=voice_raw.get("duration"),
            mime_type=voice_raw.get("mime_type"),
        )

    return TelegramMessage(
        message_id=raw["message_id"],
        chat=TelegramChat(id=chat["id"], chat_type=chat.get("type", "private")),
        from_user=from_user,
        text=raw.get("text"),
        voice=voice,
    )


def _ext_from_mime(mime_type: str | None) -> str:
    if not mime_type:
        return "ogg"
    sub = mime_type.split("/")[-1]
    return {"mpeg": "mp3", "mp4": "m4a", "x-m4a": "m4a", "ogg": "ogg", "wav": "wav"}.get(sub, sub)


def voice_prompt(path: str, duration: int | None, mime_type: str | None) -> str:
    duration_hint = f" (duration: {duration}s)" if duration else ""
    format_hint = f" The file format is {mime_type}." if mime_type else ""
    return (
        f"The user sent a voice message{duration_hint}. "
        f"The audio file is saved at: {path}{format_hint}\n\n"
        "Please transcribe this audio file using available command-line tools "
        "(e.g. whisper, ffmpeg, sox, or any STT utility you can find on this system) "
        "and then respond to what the user said. "
        "If no transcription tool is available, let the user know and ask them to type their message instead."
    )

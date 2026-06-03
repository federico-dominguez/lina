"""Low-level Telegram Bot API client (long-poll, sendMessage, editMessage, etc.)."""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

# Telegram Bot API base
TELEGRAM_API_BASE = "https://api.telegram.org"

MAX_VOICE_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_MESSAGE_LENGTH = 4096  # Telegram hard limit
_MIN_EDIT_INTERVAL_S = 0.5
_MAX_429_RETRIES = 3


# ─── Helpers ─────────────────────────────────────────────────────────────────


def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long string into chunks ≤ *max_len* characters at line breaks."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_len
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to break at last newline before max_len
        nl = text.rfind("\n", start, end)
        if nl > start:
            end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks


# ─── Data types (Telegram Bot API shapes) ────────────────────────────────────


@dataclass
class TelegramUser:
    first_name: str
    last_name: str | None
    username: str | None
    is_bot: bool = False


@dataclass
class TelegramChat:
    id: int
    chat_type: str  # "private" | "group" | "supergroup" | "channel"


@dataclass
class TelegramVoice:
    file_id: str
    file_size: int | None
    duration: int | None
    mime_type: str | None


@dataclass
class TelegramEntity:
    """Parsed entity from Telegram message."""

    type: str  # "mention" | "text_mention" | "bot_command" | etc.
    offset: int
    length: int
    user: TelegramUser | None  # populated for "text_mention" type


@dataclass
class TelegramMessage:
    message_id: int
    chat: TelegramChat
    from_user: TelegramUser | None
    text: str | None
    voice: TelegramVoice | None
    entities: list[TelegramEntity] = field(default_factory=list)
    reply_to_message_id: int | None = None


@dataclass
class TelegramCallbackQuery:
    id: str
    chat_id: int
    message_id: int
    data: str
    from_user: TelegramUser | None


@dataclass
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None
    callback_query: TelegramCallbackQuery | None = None


# ─── Client ──────────────────────────────────────────────────────────────────


class TelegramClient:
    """Minimal async Telegram Bot API client using Bot API HTTP polling."""

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
        return f"https://api.telegram.org/bot{self._token}/{method}"

    # ── Polling ──────────────────────────────────────────────────────────

    async def get_updates(self, offset: int | None) -> list[TelegramUpdate]:
        """Long-poll for new updates."""
        params: dict[str, Any] = {
            "timeout": self._poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        r = await self._http.get(self._url("getUpdates"), params=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logger.warning("getUpdates !ok: %s", data)
            return []
        return [_parse_update(u) for u in data.get("result", [])]

    # ── Send / Edit / Delete ────────────────────────────────────────────

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> int | None:
        """Send a text message. Returns the new message_id."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        payload.update(kw)
        r = await self._http.post(self._url("sendMessage"), json=payload)
        if not r.is_success:
            logger.warning("sendMessage failed: %s %s", r.status_code, r.text[:200])
            return None
        result = r.json().get("result", {})
        return result.get("message_id")

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit an existing message (throttled)."""
        now = __import__("time").time()
        key = (chat_id, message_id)
        last = self._last_edit_at.get(key, 0)
        if now - last < 2.0:
            return False  # throttled
        if not text.strip():
            return False
        self._last_edit_at[key] = now
        try:
            r = await self._http.post(
                self._url("editMessageText"),
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.TimeoutException:
            logger.info("editMessage Text timeout (chat=%s msg=%s)", chat_id, message_id)
            return False
        if r.status_code == 400 and "message is not modified" in r.text:
            return True
        return r.is_success

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Send a chat action (typing indicator, etc.)."""
        try:
            await self._http.post(
                self._url("sendChatAction"),
                json={"chat_id": chat_id, "action": action},
            )
        except Exception as exc:
            logger.debug("sendChatAction error: %s", exc)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Answer a callback query (required by Telegram to stop the loading indicator)."""
        payload: dict[str, str] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = "false"
        try:
            await self._http.post(self._url("answerCallbackQuery"), json=payload)
        except Exception:
            logger.warning("answerCallbackQuery: error for %s", callback_query_id)

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict | None = None
    ) -> None:
        """Edit only the inline keyboard of an existing message."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await self._http.post(self._url("editMessageReplyMarkup"), json=payload)
        except Exception:
            logger.warning(
                "editMessageReplyMarkup: error for msg %s in chat %s", message_id, chat_id
            )

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        """Set a reaction emoji on a message."""
        reaction: list[Any] = [] if not emoji else [{"type": "emoji", "emoji": emoji}]
        try:
            await self._http.post(
                self._url("setMessageReaction"),
                json={"chat_id": chat_id, "message_id": message_id, "reaction": reaction},
            )
        except Exception as exc:
            logger.debug("setMessageReaction error: %s", exc)

    async def get_file_path(self, file_id: str) -> str:
        """Get the file path for a Telegram file_id."""
        resp = await self._http.post(self._url("getFile"), json={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile error: {data.get('description')}")
        return data["result"]["file_path"]

    async def download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id."""
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

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict[str, Any]:
        """Get info about a chat member."""
        r = await self._http.post(
            self._url("getChatMember"),
            json={"chat_id": chat_id, "user_id": user_id},
        )
        if r.is_success:
            return r.json().get("result", {})
        return {}

    async def close(self) -> None:
        await self._http.aclose()


# ─── Parsers ─────────────────────────────────────────────────────────────────


def _parse_entities(
    raw_entities: list[dict[str, Any]],
    text: str,
) -> list[TelegramEntity]:
    """Parse Telegram message entities into clean objects."""
    entities: list[TelegramEntity] = []
    for e in raw_entities:
        user_raw = e.get("user")
        user = (
            TelegramUser(
                first_name=user_raw.get("first_name", "") if user_raw else "",
                last_name=user_raw.get("last_name") if user_raw else None,
                username=user_raw.get("username") if user_raw else None,
                is_bot=user_raw.get("is_bot", False) if user_raw else False,
            )
            if user_raw
            else None
        )

        entities.append(
            TelegramEntity(
                type=e["type"],
                offset=e["offset"],
                length=e["length"],
                user=user,
            )
        )
    return entities


def _parse_message(raw: dict[str, Any]) -> TelegramMessage:
    chat = raw["chat"]
    from_raw = raw.get("from")
    voice_raw = raw.get("voice") or raw.get("audio")
    reply_raw = raw.get("reply_to_message")

    from_user = None
    if from_raw:
        from_user = TelegramUser(
            first_name=from_raw.get("first_name", ""),
            last_name=from_raw.get("last_name"),
            username=from_raw.get("username"),
            is_bot=from_raw.get("is_bot", False),
        )

    voice = None
    if voice_raw:
        voice = TelegramVoice(
            file_id=voice_raw["file_id"],
            file_size=voice_raw.get("file_size"),
            duration=voice_raw.get("duration"),
            mime_type=voice_raw.get("mime_type"),
        )

    entities: list[TelegramEntity] = []
    if "entities" in raw:
        entities = _parse_entities(raw["entities"], raw.get("text", ""))

    reply_to_id = None
    if reply_raw:
        reply_to_id = reply_raw.get("message_id")

    return TelegramMessage(
        message_id=raw["message_id"],
        chat=TelegramChat(id=chat["id"], chat_type=chat.get("type", "private")),
        from_user=from_user,
        text=raw.get("text"),
        voice=voice,
        entities=entities,
        reply_to_message_id=reply_to_id,
    )


def _parse_update(raw: dict[str, Any]) -> TelegramUpdate:
    msg = raw.get("message")
    cq = raw.get("callback_query")
    return TelegramUpdate(
        update_id=raw["update_id"],
        message=_parse_message(msg) if msg else None,
        callback_query=_parse_callback_query(cq) if cq else None,
    )


def _parse_callback_query(raw: dict[str, Any]) -> TelegramCallbackQuery:
    from_raw = raw.get("from")
    from_user = (
        TelegramUser(
            first_name=from_raw.get("first_name", ""),
            last_name=from_raw.get("last_name"),
            username=from_raw.get("username"),
            is_bot=from_raw.get("is_bot", False),
        )
        if from_raw
        else None
    )

    return TelegramCallbackQuery(
        id=raw["id"],
        chat_id=raw["message"]["chat"]["id"],
        message_id=raw["message"]["message_id"],
        data=raw.get("data", ""),
        from_user=from_user,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def voice_prompt(path: str, duration: int | None, mime_type: str | None) -> str:
    """Build a system prompt for voice message transcription."""
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


def _ext_from_mime(mime_type: str | None) -> str:
    """Map Telegram mime types to file extensions."""
    return {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}.get(
        mime_type or "", ".oga"
    )

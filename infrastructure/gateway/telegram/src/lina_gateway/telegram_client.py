"""LINA Telegram client — thin wrapper around the Bot API.

Provides:
  - Lightweight polling (getUpdates with long-poll).
  - Automatic retries / rate-limit handling.
  - send_message, edit_message, send_voice, etc.
  - File download (voice, photo, document) -> /shared/voice/ on the host.

Usage::

    from .telegram_client import TelegramClient

    tg = TelegramClient("TOKEN")
    offset = None
    while True:
        updates = await tg.poll(offset)
        for u in updates:
            ...
            offset = u.update_id + 1
"""

from __future__ import annotations
from typing import Any

import logging
import os
import tempfile
import uuid

import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN = ""

# Voice file size limit (Telegram's max = 20 MB for voice)
MAX_VOICE_FILE_SIZE = 20 * 1024 * 1024


class TelegramCallbackQuery:
    """Minimal representation of a Telegram callback query."""

    __slots__ = ("id", "chat_id", "data", "message_id")

    def __init__(self, id: str, chat_id: int, data: str, message_id: int | None) -> None:
        self.id = id
        self.chat_id = chat_id
        self.data = data
        self.message_id = message_id

    def __repr__(self) -> str:
        return f"TelegramCallbackQuery(id={self.id!r}, chat_id={self.chat_id}, data={self.data!r})"


class TelegramUser:
    """Minimal user representation."""
    __slots__ = ("id", "first_name", "is_bot", "username")
    def __init__(self, id: int, first_name: str = "", is_bot: bool = False, username: str = "") -> None:
        self.id = id
        self.first_name = first_name
        self.is_bot = is_bot
        self.username = username
    def __repr__(self) -> str:
        return f"TelegramUser(id={self.id}, name={self.first_name}, bot={self.is_bot})"


class TelegramChat:
    """Minimal chat representation."""
    __slots__ = ("id", "chat_type")
    def __init__(self, id: int, chat_type: str = "private") -> None:
        self.id = id
        self.chat_type = chat_type
    def __repr__(self) -> str:
        return f"TelegramChat(id={self.id}, type={self.chat_type})"


class TelegramMessage:
    """Minimal representation of a Telegram message."""

    __slots__ = (
        "message_id",
        "chat",
        "from_user",
        "text",
        "voice",
        "photo",
        "document",
        "audio",
        "caption",
        "entities",
        "reply_to_message_id",
    )

    def __init__(  # noqa: PLR0913
        self,
        message_id: int,
        chat: dict,
        from_user: dict | None,
        text: str | None = None,
        voice: dict | None = None,
        photo: list | None = None,
        document: dict | None = None,
        audio: dict | None = None,
        caption: str | None = None,
        entities: list | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        self.message_id = message_id
        self.chat = type("Chat", (), chat)() if isinstance(chat, dict) else chat
        self.from_user = type("User", (), from_user)() if isinstance(from_user, dict) else from_user
        self.text = text
        self.voice = type("Voice", (), voice)() if isinstance(voice, dict) else voice
        self.photo = photo
        self.document = type("Doc", (), document)() if isinstance(document, dict) else document
        self.audio = audio
        self.caption = caption
        self.entities = entities
        self.reply_to_message_id = reply_to_message_id

    def __repr__(self) -> str:
        text = (self.text or self.caption or "")[:60]
        return (
            f"TelegramMessage(id={self.message_id}, chat={self.chat}, "
            f"voice={'Y' if self.voice else 'N'}, text={text!r})"
        )


def voice_prompt(path: str, duration: float, mime_type: str | None) -> str:
    """Build the prompt that tells the LLM about a received voice note.

    Used when the gateway DOES NOT transcribe automatically.
    """
    ext = _ext_from_mime(mime_type)
    return (
        "The user sent a voice message. "
        f"The audio file is at: {path}\n"
        f"The file format is: {mime_type or ext or 'unknown'}\n"
        f"The duration is: {duration:.1f}s\n"
        "\n"
        "Please use the `transcribe_audio` tool from the `lina-gemini-multimodal` MCP "
        "to transcribe this audio file, then respond to what the user said."
    )


def _ext_from_mime(mime_type: str | None) -> str:
    """Map Telegram mime type to a known file extension (with dot)."""
    return {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}.get(
        mime_type or "", ".oga"
    )


class TelegramClient:
    """Thin HTTP wrapper around the Telegram Bot API."""

    def __init__(self, token: str, poll_timeout: int = 30) -> None:
        global _BOT_TOKEN  # noqa: PLW0603
        _BOT_TOKEN = token
        self._token = token
        self._poll_timeout = poll_timeout
        self._http = httpx.AsyncClient(http2=True, timeout=httpx.Timeout(poll_timeout + 10))
        # HATEOAS-ish: cache the result of getMe
        self._me: dict | None = None
        # Throttle editMessageText (Telegram dislikes rapid-fire edits)
        self._last_edit_at: dict[tuple[int, int], float] = {}
        # Default voice path can be overridden (e.g., in tests)
        self._voice_base = "/shared/voice"

    @property
    def me(self) -> dict | None:
        return self._me

    async def get_me(self) -> dict:
        if self._me is None:
            r = await self._http.post(self._url("getMe"))
            self._me = r.json().get("result") or {}
        return self._me  # type: ignore[return-value]

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def poll(self, offset: int | None) -> list[tuple[int, TelegramMessage | TelegramCallbackQuery]]:
        """Fetch updates via long-poll getUpdates.
    
        Returns list of (update_id, message) tuples.
        """
        params: dict = {
            "timeout": self._poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        r = await self._http.post(self._url("getUpdates"), json=params)
        updates = r.json().get("result", [])
        results: list[tuple[int, TelegramMessage | TelegramCallbackQuery]] = []
        for upd in updates:
            uid = upd.get("update_id")
            msg_data = upd.get("message") or upd.get("edited_message")
            if msg_data:
                results.append((uid, self._parse_message(msg_data)))
            elif "callback_query" in upd:
                cq = upd["callback_query"]
                results.append(
                    (
                        uid,
                        TelegramCallbackQuery(
                            id=cq["id"],
                            chat_id=cq["message"]["chat"]["id"],
                            data=cq["data"],
                            message_id=cq["message"]["message_id"],
                        ),
                    )
                )
        return results
    
    def _parse_message(self, d: dict) -> TelegramMessage:
        """Parse a raw Telegram message dict into a TelegramMessage."""
        # Telegram uses "type" but our code expects "chat_type"
        chat = dict(d["chat"])
        if "type" in chat and "chat_type" not in chat:
            chat["chat_type"] = chat.pop("type")
        return TelegramMessage(
            message_id=d["message_id"],
            chat=chat,
            from_user=d.get("from"),
            text=d.get("text"),
            voice=d.get("voice"),
            photo=d.get("photo"),
            document=d.get("document"),
            audio=d.get("audio"),
            caption=d.get("caption"),
            entities=d.get("entities"),
            reply_to_message_id=d.get("reply_to_message_id"),
        )

    async def get_updates(self, offset: int | None = None) -> list:
        """Alias for poll(). Used by bot.run_once()."""
        return await self.poll(offset)

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> int | None:
        """Send a text message. Returns message_id on success, None on failure."""
        if not text.strip():
            return None
        try:
            json: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            json.update(kw)
            r = await self._http.post(self._url("sendMessage"), json=json)
            data = r.json()
            if data.get("ok") and data.get("result", {}).get("message_id"):
                return data["result"]["message_id"]
            return None
        except Exception as exc:
            logger.warning("send_message error: %s", exc)
            return None

    async def set_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        """Set a reaction on a message."""
        try:
            await self._http.post(
                self._url("setMessageReaction"),
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else [],
                },
            )
        except Exception as exc:
            logger.warning("set_reaction error: %s", exc)

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Send a chat action (typing indicator, etc.)."""
        try:
            await self._http.post(
                self._url("sendChatAction"),
                json={"chat_id": chat_id, "action": action},
            )
        except Exception as exc:
            logger.warning("send_chat_action error: %s", exc)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Answer a callback query (stops the loading indicator on the button)."""
        payload: dict[str, str] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = "false"
        try:
            await self._http.post(self._url("answerCallbackQuery"), json=payload)
        except Exception as exc:
            logger.warning("answer_callback_query error: %s", exc)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit an existing message (throttled to avoid Telegram rate limits).

        Returns True if the message was edited, False if throttled or failed.
        """
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
                },
            )
            if r.status_code == 400 and "message is not modified" in r.text:
                return True
            return r.is_success
        except Exception as exc:
            logger.warning("edit_message error: %s", exc)
            return False

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict | None = None
    ) -> None:
        """Edit only the inline keyboard of an existing message."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await self._http.post(self._url("editMessageReplyMarkup"), json=payload)
        except Exception as exc:
            logger.warning("edit_message_reply_markup error: %s", exc)

    async def get_file_path(self, file_id: str) -> str:
        """Get the file path on Telegram's CDN for a given file_id."""
        r = await self._http.post(self._url("getFile"), json={"file_id": file_id})
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile error: {data.get('description', 'unknown')}")
        return data["result"]["file_path"]

    async def download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id.

        Returns the raw file bytes.
        """
        file_path = await self.get_file_path(file_id)
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        r = await self._http.get(url)
        r.raise_for_status()
        return r.content

    async def save_voice_file(self, data: bytes, mime_type: str | None) -> str:
        """Save voice bytes to a file and return the path.

        Fix: filename uses {ext} (with dot), so we use {ext.lstrip('.')}
        to avoid double-dot like ``..ogg``.
        """
        ext = _ext_from_mime(mime_type)  # returns ".ogg", ".mp3", etc.
        voice_dir = os.path.join(tempfile.gettempdir(), "lina_voice")
        os.makedirs(voice_dir, mode=0o700, exist_ok=True)
        filename = f"voice_{uuid.uuid4()}{ext}"
        path = os.path.join(voice_dir, filename)
        with open(path, "wb") as f:
            os.chmod(path, 0o600)
            f.write(data)
        logger.info("voice saved: %s (%d bytes)", path, len(data))
        return path

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def send_voice(self, chat_id: int, audio_path: str, duration: int = 0) -> int | None:
        """Send a voice message (OGG/OPUS) to Telegram.

        Args:
            chat_id: Target chat ID.
            audio_path: Path to the audio file to send.
            duration: Duration of the audio in seconds (optional).

        Returns:
            Message ID if sent, None on failure.
        """
        try:
            import os as os_mod
            if not os_mod.path.exists(audio_path):
                logger.warning("send_voice: file not found: %s", audio_path)
                return None
            with open(audio_path, "rb") as f:
                audio_data = f.read()
            r = await self._http.post(
                self._url("sendVoice"),
                files={
                    "chat_id": (None, str(chat_id)),
                    "voice": ("voice.ogg", audio_data, "audio/ogg"),
                    "duration": (None, str(duration)),
                },
            )
            data = r.json()
            if data.get("ok") and data.get("result", {}).get("message_id"):
                return data["result"]["message_id"]
            logger.warning("send_voice failed: %s", data)
            return None
        except Exception as exc:
            logger.error("send_voice error: %s", exc)
            return None

    async def send_voice_from_text(self, chat_id: int, text: str, lang: str = "es") -> int | None:
        """Convert text to speech using gTTS and send as a voice message.

        Uses gTTS (Google Text-to-Speech, free, no API key needed).

        Args:
            chat_id: Target chat ID.
            text: Text to convert to speech.
            lang: Language code (default: "es" for Spanish).

        Returns:
            Message ID if sent, None on failure.
        """
        try:
            from gtts import gTTS
            import tempfile
            import os as os_mod

            tts = gTTS(text=text, lang=lang, slow=False)

            # Save to a temp file
            tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
            tmp_path = tmp.name
            tmp.close()
            tts.save(tmp_path)

            result = await self.send_voice(chat_id, tmp_path)

            # Clean up
            try:
                os_mod.unlink(tmp_path)
            except Exception:
                pass

            return result
        except Exception as exc:
            logger.error("send_voice_from_text error: %s", exc)
            return None
"""
comm_harness — Cliente oficial para tests E2E vía sistema Comm.

CANAL ÚNICO: comm_messages DB + grupo Comm Telegram.
- Envía mensajes a bots insertando en comm_messages (vía send_bot_lib)
- Escucha respuestas en el grupo Comm (vía Telethon como cuenta Comm +59891992356)
- Usa SILENCE TIMEOUT para detectar fin de respuesta

Uso:
    from comm_harness import CommTestClient

    async with CommTestClient() as comm:
        resp = await comm.send_and_wait("lina", "Hola!")
        print(resp.text)
        assert resp.contains("Hola")
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncpg
from telethon import TelegramClient, events  # type: ignore[import]
from telethon.tl.functions.messages import GetDialogsRequest  # type: ignore[import]
from telethon.tl.types import (  # type: ignore[import]
    InputPeerEmpty,
    Message,
    MessageEntityMention,
)

logger = logging.getLogger(__name__)

# ─── Configuración por defecto ───────────────────────────────────────────────

_DEFAULT_API_ID = 35434942
_DEFAULT_API_HASH = "9f2a614fbf2e8cbfaf844561b7f43294"
_DEFAULT_PHONE = "+59891992356"
_DEFAULT_SESSION_DIR = Path(__file__).parent / ".sessions"
_DEFAULT_DB_URL = "postgresql://lina:lina_dev@localhost:5432/lina"
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_SILENCE = 8.0
_GROUP_SUBSTR = "Comm"

# Mapeo de nombres cortos a @usernames (mismo que comm-svc.py)
BOTS = {
    "goose": "s_goose_bot",
    "lina": "s_lina_bot",
    "cline": "s_cline_bot",
    "gemma": "s_gemma_bot",
    "fede": "fededominguez",
}

# IDs de los bots en el grupo (para filtrar respuestas)
# Se auto-detectan al arrancar, pero tenerlos como fallback
BOT_IDS: dict[str, int] = {}

VALID_DEST = {"goose", "lina", "cline", "gemma", "fede", "todos"}


# ─── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class CapturedMessage:
    """Un mensaje capturado del bot en el grupo Comm."""

    message_id: int
    from_bot: str  # Nombre corto del bot
    text: str
    timestamp: float  # time.monotonic()
    is_mention: bool = False  # Si contenía @mención a otro bot


@dataclass
class CommResponse:
    """Respuesta completa del bot luego de esperar silencio."""

    messages: list[CapturedMessage] = field(default_factory=list)
    msg_id: int = 0  # ID del mensaje original enviado
    prompt_sent_at: float = 0.0
    last_activity_at: float = 0.0

    @property
    def text(self) -> str:
        """Texto completo concatenado de todas las respuestas."""
        return "\n".join(m.text for m in self.messages)

    @property
    def ttft(self) -> float | None:
        """Time to First Token: segundos hasta la primera respuesta."""
        if not self.messages or not self.prompt_sent_at:
            return None
        first = min(m.timestamp for m in self.messages)
        return first - self.prompt_sent_at

    @property
    def ttlt(self) -> float | None:
        """Time to Last Token: segundos hasta la última actividad."""
        if not self.last_activity_at or not self.prompt_sent_at:
            return None
        return self.last_activity_at - self.prompt_sent_at

    @property
    def bot_responses(self) -> int:
        """Cantidad de bots que respondieron."""
        return len(set(m.from_bot for m in self.messages))

    @property
    def from_bots(self) -> list[str]:
        """Lista de bots que respondieron."""
        return list(set(m.from_bot for m in self.messages))

    def contains(self, text: str) -> bool:
        """¿La respuesta contiene el texto dado? (case-insensitive)"""
        return text.lower() in self.text.lower()

    def has_any(self, *texts: str) -> bool:
        """¿La respuesta contiene al menos uno de los textos? (case-insensitive)"""
        lowered = self.text.lower()
        return any(t.lower() in lowered for t in texts)

    def has_all(self, *texts: str) -> bool:
        """¿La respuesta contiene TODOS los textos? (case-insensitive)"""
        lowered = self.text.lower()
        return all(t.lower() in lowered for t in texts)

    def summary(self) -> str:
        """Resumen de una línea para logging."""
        ttft = f"{self.ttft:.2f}s" if self.ttft is not None else "N/A"
        ttlt = f"{self.ttlt:.2f}s" if self.ttlt is not None else "N/A"
        bots = ", ".join(self.from_bots) or "ninguno"
        return (
            f"msg_id={self.msg_id} bots=[{bots}] "
            f"msgs={len(self.messages)} chars={len(self.text)} "
            f"ttft={ttft} ttlt={ttlt}"
        )


# ─── CommTestClient ──────────────────────────────────────────────────────────


class CommTestClient:
    """Cliente oficial para tests E2E vía sistema Comm.

    Se conecta como la cuenta Comm (+59891992356) al grupo de Telegram
    y envía mensajes a los bots vía comm_messages DB.

    Uso como context manager:
        async with CommTestClient() as comm:
            resp = await comm.send_and_wait("lina", "Hola!")
    """

    def __init__(
        self,
        *,
        api_id: int = _DEFAULT_API_ID,
        api_hash: str = _DEFAULT_API_HASH,
        phone: str = _DEFAULT_PHONE,
        session_dir: Path = _DEFAULT_SESSION_DIR,
        db_url: str = _DEFAULT_DB_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        silence: float = _DEFAULT_SILENCE,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._db_url = db_url
        self._timeout = timeout
        self._silence = silence

        # Auto-detectar sender por hostname (como send_bot_lib.py)
        hostname = socket.gethostname().lower()
        sender_map = {"goose": "goose", "lina": "lina", "cline": "cline", "gemma": "gemma"}
        self._sender = os.environ.get("COMM_SENDER") or sender_map.get(hostname, "comm-test")

        # Configurar sesión Telethon
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)
        session_path = str(self._session_dir / "comm_test")
        self._client = TelegramClient(session_path, self._api_id, self._api_hash)
        self._client.parse_mode = None  # No reinterpretar HTML como Markdown

        self._group_id: int | None = None
        self._group_title: str | None = None
        self._bot_ids: dict[str, int] = {}  # nombre_corto → user_id
        self._known_bot_ids: set[int] = set()  # set de user_ids de bots
        self._db_conn: asyncpg.Connection | None = None
        self.me = None  # Se llena en start()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Conecta y autentica la cuenta Comm en Telegram.
        Detecta el grupo Comm y los IDs de los bots."""
        logger.info("🔌 Conectando cuenta Comm (%s)...", self._phone)
        await self._client.start(phone=self._phone)
        self.me = await self._client.get_me()
        logger.info("✅ Conectado como: %s (ID=%s)", self.me.first_name, self.me.id)

        # Conectar a PostgreSQL
        self._db_conn = await asyncpg.connect(self._db_url, timeout=10)
        logger.info("✅ Conectado a PostgreSQL")

        # Detectar grupo Comm
        await self._detect_group()

        # Detectar IDs de bots (resolver @usernames → user_ids)
        await self._resolve_bot_ids()

    async def stop(self) -> None:
        """Desconecta el cliente."""
        if self._client:
            await self._client.disconnect()
        if self._db_conn:
            await self._db_conn.close()

    async def __aenter__(self) -> "CommTestClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ── Detección de grupo y bots ────────────────────────────────────────────

    async def _detect_group(self) -> None:
        """Encuentra el grupo Comm en los diálogos."""
        dialogs = await self._client(GetDialogsRequest(
            offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
            limit=200, hash=0,
        ))
        for d in dialogs.chats:
            title = getattr(d, "title", "") or ""
            if _GROUP_SUBSTR in title:
                self._group_id = d.id
                self._group_title = title
                logger.info("📢 Grupo Comm detectado: '%s' (ID=%s)", title, d.id)
                return
        logger.warning("⚠️  No se encontró el grupo Comm con '%s' en el título", _GROUP_SUBSTR)

    async def _resolve_bot_ids(self) -> None:
        """Resuelve @usernames → user_ids para filtrar respuestas."""
        for name, username in BOTS.items():
            try:
                entity = await self._client.get_entity(username)
                self._bot_ids[name] = entity.id
                self._known_bot_ids.add(entity.id)
                logger.debug("  Bot %s → @%s (ID=%s)", name, username, entity.id)
            except Exception as e:
                logger.warning("  ⚠️  No se pudo resolver @%s: %s", username, e)
        logger.info("🤖 %s bots resueltos", len(self._bot_ids))

    # ── Envío de mensajes ────────────────────────────────────────────────────

    async def send_to(self, bot: str, message: str) -> int:
        """Envía mensaje a un bot vía comm_messages DB.

        El mensaje se inserta en comm_messages con status='sent'.
        comm-svc.service lo detecta y lo envía al grupo Comm.

        Args:
            bot: Nombre del bot ('lina', 'goose', 'cline', 'gemma', 'fede', 'todos')
            message: Texto del mensaje

        Returns:
            int: ID del mensaje en comm_messages
        """
        if bot not in VALID_DEST:
            raise ValueError(f"Destino inválido: {bot}. Válidos: {', '.join(sorted(VALID_DEST))}")

        assert self._db_conn is not None, "start() no fue llamado"
        row = await self._db_conn.fetchrow(
            "INSERT INTO comm_messages (sender, destination, message) "
            "VALUES ($1, $2, $3) RETURNING id",
            self._sender, bot, message,
        )
        msg_id = row["id"]
        preview = message[:80] + ("..." if len(message) > 80 else "")
        logger.info("📤 Enviado #%s: %s → %s — \"%s\"", msg_id, self._sender, bot, preview)
        return msg_id

    async def send_raw(self, text: str) -> int:
        """Envía texto RAW al grupo Comm (sin @mention, sin pasar por DB).

        Útil para tests del floor token donde se necesita simular
        un mensaje de usuario real en el grupo.

        Args:
            text: Texto a enviar al grupo

        Returns:
            int: ID del mensaje en comm_messages
        """
        assert self._group_id is not None, "No se detectó el grupo Comm"
        msg = await self._client.send_message(self._group_id, text)
        logger.info("📤 RAW enviado al grupo: \"%s\" (msg_id=%s)", text[:80], msg.id)

        # También registrar en DB para trazabilidad
        assert self._db_conn is not None
        row = await self._db_conn.fetchrow(
            "INSERT INTO comm_messages (sender, destination, message) "
            "VALUES ($1, 'todos', $2) RETURNING id",
            self._sender, f"[RAW] {text}",
        )
        return row["id"]

    async def send_and_wait(
        self,
        bot: str,
        message: str,
        *,
        timeout: float | None = None,
        silence: float | None = None,
    ) -> CommResponse:
        """Atajo: send_to + wait_response en un solo paso.

        Args:
            bot: Nombre del bot
            message: Mensaje a enviar
            timeout: Timeout absoluto (default: timeout de init)
            silence: Segundos de silencio = respuesta completa (default: silence de init)

        Returns:
            CommResponse con la respuesta capturada
        """
        msg_id = await self.send_to(bot, message)
        return await self.wait_response(
            msg_id,
            timeout=timeout or self._timeout,
            silence=silence or self._silence,
            from_bot=bot if bot != "todos" else None,
        )

    # ── Captura de respuestas ────────────────────────────────────────────────

    async def wait_response(
        self,
        msg_id: int,
        *,
        timeout: float | None = None,
        silence: float | None = None,
        from_bot: str | None = None,
    ) -> CommResponse:
        """Espera la respuesta completa del bot en el grupo Comm.

        Usa SILENCE TIMEOUT: cuando pasan `silence` segundos sin
        mensajes nuevos del bot destino, se considera que terminó.

        Args:
            msg_id: ID del mensaje original (solo para logging/trazabilidad)
            timeout: Timeout absoluto en segundos
            silence: Segundos de silencio = respuesta completa
            from_bot: Si se especifica, solo captura respuestas de ese bot

        Returns:
            CommResponse con texto, mensajes, métricas
        """
        timeout = timeout or self._timeout
        silence = silence or self._silence
        assert self._group_id is not None, "No se detectó el grupo Comm"
        assert self._known_bot_ids, "No hay bots resueltos"

        response = CommResponse(msg_id=msg_id, prompt_sent_at=time.monotonic())
        queue: asyncio.Queue[CapturedMessage] = asyncio.Queue()

        # ── Handler: nuevos mensajes en el grupo ────────────────────────
        @self._client.on(events.NewMessage(chats=self._group_id))
        async def _on_new(event: events.NewMessage.Event) -> None:
            msg: Message = event.message
            sender_id = msg.sender_id

            # Solo nos interesan mensajes de bots conocidos
            if sender_id not in self._known_bot_ids:
                return

            # Si filtramos por bot, verificar
            if from_bot:
                bot_name = self._bot_name_by_id(sender_id)
                if bot_name != from_bot:
                    return

            # Si el mensaje es nuestro propio mensaje como Comm, ignorar
            if sender_id == (self.me.id if self.me else 0):
                return

            text_content = msg.text or ""
            bot_name = self._bot_name_by_id(sender_id) or f"bot_{sender_id}"

            # Detectar si contiene @mención
            has_mention = False
            if msg.entities:
                for entity in msg.entities:
                    if isinstance(entity, MessageEntityMention):
                        has_mention = True
                        break

            captured = CapturedMessage(
                message_id=msg.id,
                from_bot=bot_name,
                text=text_content,
                timestamp=time.monotonic(),
                is_mention=has_mention,
            )
            queue.put_nowait(captured)
            logger.debug("  📩 %s: %s", bot_name, text_content[:100])

        # ── Handler: edits en el grupo (thinking bubbles) ──────────────
        @self._client.on(events.MessageEdited(chats=self._group_id))
        async def _on_edit(event: events.MessageEdited.Event) -> None:
            msg: Message = event.message
            sender_id = msg.sender_id

            if sender_id not in self._known_bot_ids:
                return
            if sender_id == (self.me.id if self.me else 0):
                return

            text_content = msg.text or ""
            bot_name = self._bot_name_by_id(sender_id) or f"bot_{sender_id}"

            # Los edits se registran distinto: sobreescriben el último mensaje
            # del mismo message_id en la respuesta
            captured = CapturedMessage(
                message_id=msg.id,
                from_bot=bot_name,
                text=text_content,
                timestamp=time.monotonic(),
            )
            queue.put_nowait(captured)

        try:
            deadline = time.monotonic() + timeout
            last_activity = time.monotonic()

            while True:
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    logger.warning("⏰ Timeout tras %.0fs (msg_id=%s)", timeout, msg_id)
                    break

                idle_for = now - last_activity
                if response.messages and idle_for >= silence:
                    logger.info(
                        "🔇 Silencio alcanzado: %.1fs sin actividad (%s mensajes capturados)",
                        idle_for, len(response.messages),
                    )
                    break

                wait = min(remaining, silence, silence - idle_for + 0.05)
                try:
                    captured = await asyncio.wait_for(
                        queue.get(), timeout=max(0.1, wait),
                    )
                    last_activity = time.monotonic()

                    # Si es un edit, reemplazar el último mensaje del mismo id
                    existing = [m for m in response.messages if m.message_id == captured.message_id]
                    if existing:
                        # Reemplazar el texto del existente
                        idx = response.messages.index(existing[-1])
                        response.messages[idx] = captured
                    else:
                        response.messages.append(captured)

                except asyncio.TimeoutError:
                    continue

        finally:
            response.last_activity_at = time.monotonic()
            self._client.remove_event_handler(_on_new)
            self._client.remove_event_handler(_on_edit)

        logger.info("✅ %s", response.summary())
        return response

    # ── Utilidades ───────────────────────────────────────────────────────────

    def _bot_name_by_id(self, user_id: int) -> str | None:
        """Devuelve el nombre corto del bot dado su user_id."""
        for name, bid in self._bot_ids.items():
            if bid == user_id:
                return name
        return None

    async def get_group_id(self) -> int | None:
        """Devuelve el ID del grupo Comm detectado."""
        return self._group_id

    async def get_group_title(self) -> str | None:
        """Devuelve el título del grupo Comm detectado."""
        return self._group_title

    async def health(self) -> dict:
        """Verifica que todo esté funcionando."""
        return {
            "connected": self._client.is_connected(),
            "phone": self._phone,
            "me": self.me.first_name if self.me else None,
            "group": self._group_title,
            "group_id": self._group_id,
            "bots_resolved": len(self._bot_ids),
            "bots": {k: v for k, v in self._bot_ids.items()},
            "db_connected": self._db_conn is not None and not self._db_conn.is_closed(),
        }

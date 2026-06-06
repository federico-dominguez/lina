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
import uuid
from collections import defaultdict

import httpx

from .boot_hook import get_smart_context, save_message, save_token_usage, save_trace
from .circuit_breaker import CircuitBreaker
from .commands.audit import handle_audit
from .config import BotConfig, SharedConfig
from .episodic_memory import EpisodicMemory
from .feedback import FeedbackManager
from .floor import FloorTokenManager, _is_higher_priority
from .formatter import (
    format_tool_status,
    format_with_thinking,
    markdown_to_telegram_html,
    split_message,
)
from .goose_client import EventType, GoosedClient, TokenState
from .heartbeat import HeartbeatService
from .observe import ObserveServer
from .pacer import StreamingBubble
from .profiles import BotProfileLoader
from .telegram_client import (
    MAX_VOICE_FILE_SIZE,
    TelegramCallbackQuery,
    TelegramChat,
    TelegramClient,
    TelegramMessage,
    TelegramUser,
)
from .transcriber import transcribe_audio

logger = logging.getLogger(__name__)

_STOP_COMMANDS = {"/stop", "stop", "/Stop", "Stop", "/STOP", "STOP"}
_TG_CHAR_LIMIT = 4096
_OVERFLOW_MARGIN = 200  # start splitting when content approaches the limit


def _btn(label: str, action: str, agent_id: str) -> dict:
    """Build an inline keyboard button for agent actions."""
    callback_data = f"{action}:{agent_id}" if agent_id else f"{action}:"
    return {"text": label, "callback_data": callback_data}


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
    """A single bot instance: one Telegram client + one goosed endpoint.

    Multiple Bot instances can coexist in the same process (multi-bot gateway),
    each with its own Telegram bot token and goosed URL.
    """

    def __init__(self, bot_cfg: BotConfig, shared: SharedConfig) -> None:
        self._cfg = bot_cfg
        self._shared = shared
        self._name = bot_cfg.name
        self._tg = TelegramClient(bot_cfg.bot_token, poll_timeout=shared.poll_timeout)
        self._goosed = GoosedClient(
            base_url=bot_cfg.goosed_url,
            secret=bot_cfg.goosed_secret,
            connect_timeout=shared.goosed_connect_timeout,
            read_timeout=shared.goosed_read_timeout,
        )
        # chat_id → session_id (persistent per chat)
        self._sessions: dict[int, str] = {}
        # chat_id → active cancel event (one reply at a time per chat)
        self._cancels: dict[int, asyncio.Event] = defaultdict(asyncio.Event)
        # chat_id → True if currently processing
        self._busy: dict[int, bool] = {}
        # chat_ids for which session context has been injected this process lifetime
        self._sessions_initialized: set[int] = set()
        self._observer: ObserveServer | None = None
        # chat_id → last assistant response text (for /voz command)
        self._last_response: dict[int, str] = {}
        # chat_id → True if we should send voice response (voice thread)
        self._voice_mode: dict[int, bool] = {}
        # Floor token manager for multi-bot conversation turn control
        self._floor: FloorTokenManager = FloorTokenManager(shared.lina_db_url)
        # Orchestrator for intelligent routing (initialized on first _handle with DB)
        self._orchestrator = None  # Inicializado en _handle si hay DB
        # Fase 4 — Memoria Compartida y Evolución
        self._memory: EpisodicMemory | None = None
        self._feedback: FeedbackManager | None = None
        self._profile_prompt: str = ""

        if shared.lina_db_url:
            self._memory = EpisodicMemory(shared.lina_db_url)
            self._feedback = FeedbackManager(shared.lina_db_url)
            try:
                profile = BotProfileLoader.load(self._name)
                self._profile_prompt = BotProfileLoader.format_system_prompt(profile)
                logger.info(
                    "%s: loaded profile (%s, %s)", self._name, profile.personality, profile.tone
                )
            except Exception as exc:
                logger.warning("%s: failed to load profile: %s", self._name, exc)
        # ID del floor token activo (si se adquirió), por chat_id
        self._floor_token_ids: dict[int, int] = {}
        # Floor timeout configurable por bot (turn-timeout escalado)
        self._floor_timeout: float = getattr(bot_cfg, "floor_timeout", 30.0)
        # Heartbeat service
        self._heartbeat: HeartbeatService | None = None
        # Circuit breaker
        self._breaker: CircuitBreaker | None = None

        # Initialize heartbeat and circuit breaker if DB URL is available
        if shared.lina_db_url:
            self._heartbeat = HeartbeatService(
                shared.lina_db_url,
                self._name,
                interval=getattr(bot_cfg, "heartbeat_interval", 30.0),
            )
            self._breaker = CircuitBreaker(
                shared.lina_db_url,
                self._name,
                threshold=getattr(bot_cfg, "circuit_breaker_threshold", 5),
            )

    @property
    def tg(self) -> TelegramClient:
        """Expose the Telegram client for use outside the bot loop (e.g. boot hook)."""
        return self._tg

    def _session_id(self, chat_id: int) -> str:
        # Allow overriding with a fixed session ID (set via GOOSE_FIXED_SESSION_ID env var).
        if self._cfg.fixed_session_id:
            return self._cfg.fixed_session_id
        if chat_id not in self._sessions:
            self._sessions[chat_id] = f"telegram-{chat_id}"
        return self._sessions[chat_id]

    def _should_respond(self, msg: TelegramMessage) -> bool:
        """Decide if this bot should respond to *msg*.

        Rules:
        1. Private chat → always respond.
        2. Group / supergroup → respond if:
           a. Bot is @mentioned in the text.
           b. Sender is another bot (bot-to-bot bridge).
        """
        # Private — always
        if msg.chat.chat_type == "private":
            return True

        if msg.chat.chat_type not in ("group", "supergroup"):
            return False  # channel or unknown → no

        # Comm bridge (HTTP) → siempre responder (mensaje directo a este bot)
        if msg.from_user and msg.from_user.id == 8887121852:
            return True

        # Bot-to-bot: responder SOLO si este bot es mencionado
        if msg.from_user and msg.from_user.is_bot:
            if msg.entities:
                text_lower = (msg.text or "").lower()
                for ent in msg.entities:
                    ent_text = text_lower[
                        ent.get("offset", 0) : ent.get("offset", 0) + ent.get("length", 0)
                    ]
                    if self._is_this_bot(ent_text.lstrip("@")):
                        return True
            # Fallback: @mention en texto plano
            if msg.text and self._is_this_bot((msg.text or "").lstrip("@")):
                return True
            return False

        # @mention check
        if msg.entities:
            text = (msg.text or "").lower()
            for ent in msg.entities:
                if (isinstance(ent, dict) and ent.get("type") == "mention") or (
                    not isinstance(ent, dict) and ent.type == "mention"
                ):
                    mentioned = text[ent.offset : ent.offset + ent.length].lstrip("@")
                    if self._is_this_bot(mentioned):
                        import re as _re_mod

                        _prio_mentioned = _re_mod.findall(
                            r"@s_([a-z]+)_bot", msg.text.lower() if msg.text else ""
                        )
                        _my_name = self._name.lower()
                        if len(_prio_mentioned) > 1 and _my_name in _prio_mentioned:
                            for _other in _prio_mentioned:
                                if _other != _my_name and _is_higher_priority(_other, _my_name):
                                    logger.debug("%s: skipping - %s has priority", _my_name, _other)
                                    return False
                    return True
                elif (isinstance(ent, dict) and ent.get("type") == "text_mention") or (
                    not isinstance(ent, dict) and ent.type == "text_mention"
                ):
                    # text_mention to a user/bot by ID — assume it's us
                    return True

        # ── Fallback: detectar @s_botname en texto aunque no haya entities ──
        if msg.text:
            import re
            _mentions = re.findall(r'@s_([a-z]+)_bot', msg.text.lower())
            for _m in _mentions:
                if self._is_this_bot(_m):
                    return True
        
        return False

    def _is_this_bot(self, username: str) -> bool:
        """Check if *username* refers to this bot (by Telegram @ username)."""
        # Map canonical bot names to their Telegram @usernames (no @ prefix)
        mapping = {
            "lina": "s_lina_bot",
            "goose": "s_goose_bot",
            "cline": "s_cline_bot",
            "gemma": "s_gemma_bot",
        }
        my_name = self._name.lower()
        expected = mapping.get(my_name)
        return username.lower() == expected

    async def _handle(self, msg: TelegramMessage) -> None:
        # Comm messages (from HTTP bridge) → redirect to real Comm group
        if msg.from_user and getattr(msg.from_user, "id", None) == 8887121852:
            chat_id = -5110614353
            is_comm_msg = True
        else:
            chat_id = msg.chat.id
            is_comm_msg = False
        text = msg.text or ""

        # ── Mention filter: skip if not addressed to this bot ──────────────
        if not self._should_respond(msg):
            logger.debug(
                "Ignoring message %s in %s chat %s (not addressed)",
                msg.message_id,
                msg.chat.chat_type,
                chat_id,
            )
            return

        # ── /stop ──────────────────────────────────────────────────────
        if text.strip() in _STOP_COMMANDS:
            if self._busy.get(chat_id):
                self._cancels[chat_id].set()
                await self._tg.send_message(chat_id, "⛔ Deteniendo.")
            else:
                await self._tg.send_message(chat_id, "ℹ️ No hay ninguna tarea en curso.")
            return

        # ── /agents ───────────────────────────────────────────────
        if text.strip() == "/agents":
            await self._handle_agents(chat_id)
            return

        # ── /feedback ──────────────────────────────────────────
        if text.startswith("/feedback"):
            parts = text.split()
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "Uso: /feedback <bot> <rating 1-5> [comentario]\n"
                    "Ej: /feedback cline 5 Excelente código!",
                )
                return

            target = parts[1].lower().replace("@s_", "").replace("_bot", "")
            try:
                rating = int(parts[2])
            except (IndexError, ValueError):
                await self._tg.send_message(chat_id, "❌ Rating debe ser un número del 1 al 5")
                return

            comment = " ".join(parts[3:]) if len(parts) > 3 else ""

            fb_conv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-chat-{chat_id}"))
            if self._feedback:
                success = await self._feedback.submit(
                    from_bot=self._name.lower(),
                    to_bot=target,
                    conversation_id=fb_conv_id,
                    rating=rating,
                    comment=comment,
                )
                if success:
                    await self._tg.send_message(
                        chat_id, f"✅ Feedback registrado: @{target} → ⭐ {rating}/5"
                    )
                else:
                    await self._tg.send_message(chat_id, "❌ Error al guardar feedback")
            else:
                await self._tg.send_message(chat_id, "❌ Feedback no disponible (sin DB)")
            return

        # ── /ratings ──────────────────────────────────────────
        if text.startswith("/ratings"):
            if self._feedback:
                summary = await self._feedback.get_team_summary()
                await self._tg.send_message(chat_id, summary)
            else:
                await self._tg.send_message(chat_id, "❌ Ratings no disponibles (sin DB)")
            return

        # ── /cline ── muestra estado de CLINE (cross-agent) ────────────
        if text.strip().lower() in ("/cline", "cline"):
            await self._handle_cline_status(chat_id)
            return

        # ── /lina ── muestra estado de LINA (cross-agent) ──────────────
        if text.strip().lower() in ("/lina", "lina"):
            await self._handle_lina_status(chat_id)
            return

        # ── /status <agent_id> ────────────────────────────────────────────
        if text.strip().lower().startswith("/status"):
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/status &lt;agent_id&gt;</code>",
                )
            else:
                await self._handle_agent_status(chat_id, parts[1].strip())
            return

        # ── /events <agent_id> ────────────────────────────────────────────
        if text.strip().lower().startswith("/events"):
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/events &lt;agent_id&gt;</code>",
                )
            else:
                await self._handle_agent_events(chat_id, parts[1].strip())
            return

        # ── /instruct <agent_id> <texto> ────────────────────────────────
        # Expands to a precise LINA prompt so lina-orchestrator handles the tool call.
        if text.strip().lower().startswith("/instruct"):
            parts = text.strip().split(None, 2)
            if len(parts) < 3:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/instruct &lt;agent_id&gt; &lt;instrucción&gt;</code>",
                )
                return
            _, agent_id, instruction = parts
            text = (
                f"Enviá la siguiente instrucción al sub-agente con ID '{agent_id}' "
                f"usando send_instruction() del MCP lina-orchestrator: {instruction}"
            )
            # falls through to normal goosed flow

        # ── /kill <agent_id> ─────────────────────────────────────────────
        if text.strip().lower().startswith("/kill"):
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/kill &lt;agent_id&gt;</code>",
                )
            else:
                await self._kill_agent(chat_id, parts[1].strip())
            return

        # ── /pause <agent_id> ────────────────────────────────────────────
        if text.strip().lower().startswith("/pause"):
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/pause &lt;agent_id&gt;</code>",
                )
            else:
                await self._pause_agent(chat_id, parts[1].strip())
            return

        # ── /resumen ── audio summary via TTS ─────────────────────────
        if text.strip().lower() == "/resumen":
            cancel_event = asyncio.Event()
            self._cancels[chat_id] = cancel_event
            self._busy[chat_id] = True
            try:
                session_id = self._session_id(chat_id)
                try:
                    session_id, _ = await self._goosed.ensure_session(session_id)
                except Exception as exc:
                    logger.warning("Could not ensure session for /resumen: %s", exc)
                await self._tg.send_message(chat_id, "🎙️ Generando resumen de audio...")
                prompt = (
                    "Generá un RESUMEN AUDIO de nuestra conversación reciente en este chat. "
                    "Incluí SOLO la información importante: decisiones tomadas, conclusiones, "
                    "hallazgos clave. Ignorá comandos internos, mensajes de sistema y herramientas. "
                    "Respondé en español neutro, en un formato pensado para ser LEÍDO EN VOZ ALTA "
                    "por un sistema text-to-speech. Usá frases fluidas y naturales. "
                    "Máximo 200 palabras. Empezá directamente con el resumen, "
                    "sin introducciones ni frases como 'Aquí tienes el resumen'."
                )
                body_acc = ""
                async for event in self._goosed.reply_stream(session_id, prompt):
                    if cancel_event.is_set():
                        break
                    if event.event_type == EventType.MESSAGE:
                        for item in event.contents:
                            if item.content_type == "text" and item.text:
                                body_acc += item.text
                    if event.event_type == EventType.FINISH:
                        break
                if body_acc.strip():
                    await self._tg.send_voice_from_text(chat_id, body_acc.strip())
                else:
                    await self._tg.send_message(chat_id, "⚠️ No se pudo generar el resumen.")
            finally:
                self._busy[chat_id] = False
            return

        # ── /resume <agent_id> ───────────────────────────────────────────
        if text.strip().lower().startswith("/resume"):
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/resume &lt;agent_id&gt;</code>",
                )
            else:
                await self._resume_agent(chat_id, parts[1].strip())
            return

        # ── /replan <agent_id> <nuevo goal> ─────────────────────────────
        if text.strip().lower().startswith("/replan"):
            parts = text.strip().split(None, 2)
            if len(parts) < 3:
                await self._tg.send_message(
                    chat_id,
                    "⚠️ Uso: <code>/replan &lt;agent_id&gt; &lt;nuevo objetivo&gt;</code>",
                )
                return
            await self._replan_agent(chat_id, parts[1].strip(), parts[2].strip())
            return

        # ── /audit ── consulta de acciones recientes ─────────────────────
        if text.strip().lower().startswith("/audit"):
            args = text.strip()[len("/audit") :].strip()
            await handle_audit(chat_id, args, self._tg, self._shared.lina_db_url)
            return

        # ── /voz ── respond with voice (TTS) ──────────────────────────
        if text.strip().lower() == "/voz":
            last = self._last_response.get(chat_id)
            if last:
                await self._tg.send_message(chat_id, "🔊 Convirtiendo a voz...")
                await self._tg.send_voice_from_text(chat_id, last)
            else:
                await self._tg.send_message(
                    chat_id, "ℹ️ No hay respuesta previa para convertir a voz."
                )
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
                status_id = await self._tg.send_message(chat_id, "🎤 Transcribiendo audio...")
                transcription = await transcribe_audio(path)
                await self._tg.edit_message(chat_id, status_id, "🎤 Audio transcrito.")
                text = f"[transcripción automática] {transcription}"
            except Exception as exc:
                logger.error("Failed to process voice file: %s", exc)
                await self._tg.send_message(chat_id, "⚠️ No pude procesar la nota de voz.")
                return

        if not text.strip():
            return

        # ── Floor token: evitar que varios bots respondan a la vez ──────
        # Solo en grupos/supergrupos donde hay múltiples bots
        conv_id = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-chat-{chat_id}")
        )  # UUID determinista por chat
        # Comm bridge: saltar floor token, contexto, orchestrador — es un relay de sistema, no conversación
        if is_comm_msg:
            logger.debug("Comm bridge: modo relay — sin floor, sin contexto, sin grupo")
        elif msg.chat.chat_type in ("group", "supergroup") and self._floor.is_enabled:
            token = await self._floor.try_acquire(
                self._name.lower(),
                conv_id,
                timeout=self._cfg.floor_timeout,
            )
            if not token.granted:
                # Otro bot tiene el turno — encolamos el mensaje como pendiente
                logger.debug(
                    "%s: floor ocupado por %s, encolando mensaje %s",
                    self._name,
                    token.active_bot,
                    msg.message_id,
                )
                await self._floor.enqueue_message(
                    conv_id,
                    from_bot="user",
                    to_bot=self._name.lower(),
                    message=text,
                )
                await self._tg.send_message(
                    chat_id,
                    f"⏳ {self._name} esperando turno… ({token.active_bot} está respondiendo)",
                )
                return

            # Token adquirido — guardamos el ID para liberarlo después
            self._floor_token_ids[chat_id] = token.token_id
            logger.debug(
                "%s: floor adquirido (id=%s) conv=%s",
                self._name,
                token.token_id,
                conv_id,
            )

            # ── Contexto acumulativo: inyectar historial de la conversación ──
            # Siempre que se adquiere el floor (nuevo o renovado), recuperamos
            # los últimos mensajes entre bots para mantener coherencia.
            context_msgs = await self._floor.get_context_messages(conv_id, limit=8)
            if context_msgs:
                ctx_lines = ["--- Contexto conversacional ---"]
                for cm in context_msgs:
                    label = "Usuario" if cm.from_bot == "user" else cm.from_bot.upper()
                    preview = cm.message[:300].replace("\n", " ")
                    ctx_lines.append(f"  {label}: {preview}")
                ctx_text = "\n".join(ctx_lines)
                text = f"{text}\n\n{ctx_text}"
                logger.debug(
                    "%s: contexto inyectado (%d mensajes previos)",
                    self._name,
                    len(context_msgs),
                )

            # Marcar mensajes pendientes como procesados (ACK)
            pending = await self._floor.get_pending_messages(conv_id, to_bot=self._name.lower())
            for pm in pending:
                await self._floor.ack_message(pm["id"])

            # ── Orchestrator: routing inteligente ──
            if self._shared.lina_db_url and msg.chat.chat_type in ("group", "supergroup"):
                if self._orchestrator is None:
                    from .orchestrator import ConversationRouter

                    self._orchestrator = ConversationRouter()

                decision = self._orchestrator.route(text, self._name.lower())
                if decision.should_route:
                    logger.debug(
                        "%s: redirigiendo a %s (%s)",
                        self._name,
                        decision.target_bot,
                        decision.reason,
                    )
                    # Liberar floor antes de redirigir
                    floor_token_id = self._floor_token_ids.pop(chat_id, None)
                    if floor_token_id is not None:
                        asyncio.create_task(
                            self._floor.release(
                                floor_token_id,
                                conv_id,
                                self._name.lower(),
                                reason="routing",
                            ),
                            name=f"floor-release-{chat_id}",
                        )
                    # Encolar para el bot destino via conversation_messages
                    # Prefijar con @mention para que el bot destino lo detecte
                    target_mention = (
                        f"@{decision.target_username}"
                        if decision.target_username
                        else f"@{decision.target_bot}"
                    )
                    await self._floor.enqueue_message(
                        conv_id,
                        from_bot=self._name.lower(),
                        to_bot=decision.target_bot,
                        message=f"{target_mention} {text}",
                    )
                    await self._tg.send_message(
                        chat_id,
                        f"@{decision.target_username} {text}",
                    )
                    return

        # ── Goosed health check ─────────────────────────────────────────
        if not await self._goosed.is_alive():
            # goosed may be mid-restart — wait briefly before giving up
            status_id = await self._tg.send_message(
                chat_id, f"⏳ {self._name} se está reiniciando, un momento..."
            )
            recovered = await self._wait_for_goosed()
            if not recovered:
                await self._tg.edit_message(
                    chat_id,
                    status_id,
                    f"⚠️ {self._name} no está disponible. Intentá de nuevo en unos segundos.",
                )
                # Liberar floor ante fallo de goosed
                floor_token_id = self._floor_token_ids.pop(chat_id, None)
                if floor_token_id is not None:
                    asyncio.create_task(
                        self._floor.release(
                            floor_token_id,
                            conv_id,
                            self._name.lower(),
                            reason="goosed_unavailable",
                        ),
                        name=f"floor-release-{chat_id}",
                    )
                return
            await self._tg.edit_message(chat_id, status_id, f"✅ {self._name} de vuelta.")

        # ── Typing indicator ────────────────────────────────────────────
        if not is_comm_msg:
            await self._tg.send_chat_action(chat_id, "typing")
            await self._tg.set_reaction(chat_id, msg.message_id, "⚡")

        # ── Reset cancel token ──────────────────────────────────────────
        cancel_event = asyncio.Event()
        self._cancels[chat_id] = cancel_event
        self._busy[chat_id] = True

        # ── Fase 4: Inyectar contexto de memoria episódica ──
        episodic_context = ""
        if self._memory and msg.chat.chat_type in ("group", "supergroup"):
            try:
                episodic_context = await self._memory.get_context(
                    text, limit=2, min_similarity=0.65
                )
            except Exception as exc:
                logger.debug("%s: episodic memory context failed: %s", self._name, exc)

        # Si hay contexto episódico, extender el mensaje
        if episodic_context:
            text = f"{text}\n\n[Contexto]\n{episodic_context}"
            logger.debug(
                "%s: injected episodic context (%d chars)", self._name, len(episodic_context)
            )

        # ── Fase 4: Inyectar perfil de personalidad ──
        if self._profile_prompt and msg.chat.chat_type in ("group", "supergroup"):
            text = f"[{self._profile_prompt}]\n\n{text}"

        try:
            await self._reply(chat_id, msg.message_id, text, cancel_event, is_comm_msg=is_comm_msg)
        finally:
            self._busy[chat_id] = False
            # Liberar floor token si lo habíamos adquirido
            floor_token_id = self._floor_token_ids.pop(chat_id, None)
            if floor_token_id is not None:
                asyncio.create_task(
                    self._floor.release(
                        floor_token_id,
                        conv_id,
                        self._name.lower(),
                        reason="voluntary",
                    ),
                    name=f"floor-release-{chat_id}",
                )
            # Clear reaction on original message
            await self._tg.set_reaction(chat_id, msg.message_id, "")

        # ── Fase 4: Almacenar en memoria episódica ──
        if self._memory and msg.chat.chat_type in ("group", "supergroup"):
            response_text = self._last_response.get(chat_id, "")
            if response_text:
                try:
                    await self._memory.store(
                        conversation_id=conv_id,
                        bot_name=self._name.lower(),
                        summary=response_text[:500],
                        topics=[],
                        turn_count=1,
                    )
                except Exception as exc:
                    logger.debug("%s: failed to store conversation memory: %s", self._name, exc)

    # ── Agent management actions ─────────────────────────────────────────

    async def _kill_agent(self, chat_id: int, agent_id: str) -> None:
        """Kill a running agent."""
        try:
            import asyncpg

            conn = await asyncpg.connect(self._shared.lina_db_url, timeout=5)
            try:
                await conn.execute(
                    "UPDATE agent_sessions SET status='killed', ended_at=NOW(), updated_at=NOW()"
                    " WHERE id=$1 AND status IN ('running','pending')",
                    agent_id,
                )
            finally:
                await conn.close()
            await self._tg.send_message(chat_id, f"⛔ Agente <code>{agent_id[:8]}</code> detenido.")
        except Exception as exc:
            await self._tg.send_message(chat_id, f"⚠️ Error al detener: <code>{exc}</code>")

    async def _pause_agent(self, chat_id: int, agent_id: str) -> None:
        """Pause a running agent."""
        try:
            import asyncpg

            conn = await asyncpg.connect(self._shared.lina_db_url, timeout=5)
            try:
                await conn.execute(
                    "UPDATE agent_sessions SET status='paused', updated_at=NOW()"
                    " WHERE id=$1 AND status='running'",
                    agent_id,
                )
            finally:
                await conn.close()
            await self._tg.send_message(chat_id, f"⏸️ Agente <code>{agent_id[:8]}</code> pausado.")
        except Exception as exc:
            await self._tg.send_message(chat_id, f"⚠️ Error al pausar: <code>{exc}</code>")

    async def _resume_agent(self, chat_id: int, agent_id: str) -> None:
        """Resume a paused agent."""
        try:
            import asyncpg

            conn = await asyncpg.connect(self._shared.lina_db_url, timeout=5)
            try:
                await conn.execute(
                    "UPDATE agent_sessions SET status='running', updated_at=NOW()"
                    " WHERE id=$1 AND status='paused'",
                    agent_id,
                )
            finally:
                await conn.close()
            await self._tg.send_message(chat_id, f"▶️ Agente <code>{agent_id[:8]}</code> reanudado.")
        except Exception as exc:
            await self._tg.send_message(chat_id, f"⚠️ Error al reanudar: <code>{exc}</code>")

    async def _replan_agent(self, chat_id: int, agent_id: str, new_goal: str) -> None:
        """Replan: actualiza el goal de un agente en la DB y lo reanuda si está pausado."""
        try:
            import asyncpg

            conn = await asyncpg.connect(self._shared.lina_db_url, timeout=5)
            try:
                result = await conn.execute(
                    "UPDATE agent_sessions SET goal=$2, updated_at=NOW()"
                    " WHERE id=$1 AND status IN ('running','paused','pending')",
                    agent_id,
                    new_goal,
                )
            finally:
                await conn.close()

            goal_short = new_goal[:60] + ("…" if len(new_goal) > 60 else "")
            if result and "1" in result:
                await self._tg.send_message(
                    chat_id,
                    f"🔄 Agente <code>{agent_id[:8]}</code> replanificado:\n   <i>{goal_short}</i>",
                )
            else:
                await self._tg.send_message(
                    chat_id,
                    f"⚠️ No se encontró agente activo con ID <code>{agent_id[:8]}</code>",
                )
        except Exception as exc:
            await self._tg.send_message(chat_id, f"⚠️ Error al replanificar: <code>{exc}</code>")

    # ── HumanInputRequest: detect approval needs & attach buttons ──────

    _APPROVAL_PATTERNS = (
        "¿apruebas",
        "apruebas?",
        "¿confirmas",
        "confirmas?",
        "¿procedo",
        "¿continúo",
        "¿te parece bien",
        "necesito tu aprobación",
        "necesito aprobación",
        "¿está bien",
        "¿estás de acuerdo",
        "¿puedo",
        "¿quieres que",
        "human_input_request",
        "approval_request",
        "requiere aprobación",
        "requiere tu aprobación",
        "¿sí o no",
        "¿procedemos",
    )

    async def _maybe_attach_approval_buttons(
        self,
        chat_id: int,
        msg_id: int,
        text: str,
        session_id: str,
        user_text: str,
    ) -> None:
        """If LINA's response contains an approval request, attach inline buttons.

        Detects patterns like "¿Apruebas?", "Necesito aprobación", etc.
        and replaces the message with one that has ✅ Sí / ❌ No buttons.
        """
        lower = (text or "").lower()
        if not any(p in lower for p in self._APPROVAL_PATTERNS):
            return

        # Approval request detected! Attach buttons
        buttons = {
            "inline_keyboard": [
                [
                    {"text": "✅ Sí, aprobar", "callback_data": "approve:yes"},
                    {"text": "❌ No", "callback_data": "reject:no"},
                    {"text": "✏️ Modificar", "callback_data": "approve:modify"},
                ],
            ]
        }
        # Edit the message to add buttons under the existing text
        await self._tg.edit_message_reply_markup(chat_id, msg_id, buttons)
        logger.info(
            "Approval buttons attached — chat=%s msg=%s session=%s",
            chat_id,
            msg_id,
            session_id,
        )

    async def _handle_cline_status(self, chat_id: int) -> None:
        """Muestra el estado de CLINE — cross-agent status check."""
        db_url = self._shared.lina_db_url
        if not db_url:
            await self._tg.send_message(chat_id, "⚠️ lina-db no disponible.")
            return
        try:
            import asyncpg

            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                # Count orders by status for CLINE
                orders = await conn.fetchrow(
                    """SELECT 
                        COUNT(*) FILTER (WHERE status='pending') as pending,
                        COUNT(*) FILTER (WHERE status='running') as running,
                        COUNT(*) FILTER (WHERE status='completed') as completed,
                        COUNT(*) FILTER (WHERE status='failed') as failed
                    FROM cline_commands"""
                )
                # Last completed order
                last = await conn.fetchrow(
                    """SELECT id, response, completed_at 
                    FROM cline_commands WHERE status='completed' 
                    ORDER BY completed_at DESC LIMIT 1"""
                )
            finally:
                await conn.close()
        except Exception as e:
            await self._tg.send_message(chat_id, f"⚠️ Error: {e}")
            return

        parts = [
            "<b>🤖 Estado de CLINE</b>",
            "",
            f"• Pendientes: <b>{orders['pending'] or 0}</b>",
            f"• En ejecución: <b>{orders['running'] or 0}</b>",
            f"• Completadas: <b>{orders['completed'] or 0}</b>",
            f"• Fallidas: <b>{orders['failed'] or 0}</b>",
        ]
        if last and last["completed_at"]:
            parts.append("")
            parts.append(
                f"✅ Última: #{last['id']} — <i>{last['response'][:80] if last['response'] else 'sin detalle'}</i>"
            )
        await self._tg.send_message(chat_id, "\n".join(parts))

    async def _handle_lina_status(self, chat_id: int) -> None:
        """Muestra el estado de LINA — cross-agent status check."""
        db_url = self._shared.lina_db_url
        if not db_url:
            await self._tg.send_message(chat_id, "⚠️ lina-db no disponible.")
            return
        try:
            import asyncpg

            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                # LINA's orders created (write_cline_command)
                orders = await conn.fetchrow(
                    """SELECT 
                        COUNT(*) FILTER (WHERE status='pending') as pending,
                        COUNT(*) FILTER (WHERE status='running') as running,
                        COUNT(*) FILTER (WHERE status='completed') as completed
                    FROM cline_commands"""
                )
                # Last session
                last_session = await conn.fetchrow(
                    """SELECT session_id, summary, created_at 
                    FROM session_summaries 
                    ORDER BY created_at DESC LIMIT 1"""
                )
            finally:
                await conn.close()
        except Exception as e:
            await self._tg.send_message(chat_id, f"⚠️ Error: {e}")
            return

        parts = [
            f"<b>🩷 Estado de {self._name}</b>",
            "",
            f"• Órdenes creadas: <b>{orders['completed'] or 0}</b> completadas, <b>{orders['pending'] or 0}</b> pendientes",
            f"• En ejecución: <b>{orders['running'] or 0}</b>",
        ]
        if last_session and last_session["created_at"]:
            parts.append("")
            parts.append(
                f"📚 Última sesión: <i>{last_session['summary'][:100] if last_session['summary'] else 'sin resumen'}</i>"
            )
        await self._tg.send_message(chat_id, "\n".join(parts))

    async def _handle_agents(self, chat_id: int, edit_msg_id: int | None = None) -> None:
        """Dashboard live editable de sub-agentes con botones inline.

        Sin pasar por goosed — consulta directo a lina-db para velocidad.
        Si *edit_msg_id* se pasa, edita ese mensaje en lugar de crear uno nuevo.
        """
        db_url = self._shared.lina_db_url
        if not db_url:
            await self._tg.send_message(chat_id, "⚠️ lina-db no disponible.")
            return
        try:
            import asyncpg

            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    "SELECT id, role, goal, status, elapsed_seconds"
                    " FROM agent_sessions_active ORDER BY started_at DESC NULLS LAST LIMIT 10"
                )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            await self._tg.send_message(chat_id, f"⚠️ Error: <code>{exc}</code>")
            return

        # ── Build message text ──────────────────────────────────────────────
        if not rows:
            text = "ℹ️ No hay agentes activos."
            reply_markup = {"inline_keyboard": [[_btn("🔄 Actualizar", "refresh", "")]]}
            if edit_msg_id:
                await self._tg.edit_message(chat_id, edit_msg_id, text)
                await self._tg.edit_message_reply_markup(chat_id, edit_msg_id, reply_markup)
            else:
                await self._tg.send_message(chat_id, text, reply_markup=reply_markup)
            return

        now_ts = int(__import__("time").time())
        lines = [f"<b>🤖 Agentes activos</b>  <code>{now_ts:08x}</code>"]
        inline_keyboard = []
        for r in rows:
            aid_short = r["id"][:8]
            elapsed = int(r["elapsed_seconds"] or 0)
            mins, secs = divmod(elapsed, 60)
            goal_short = r["goal"][:50] + ("…" if len(r["goal"]) > 50 else "")
            status_icon = {
                "running": "🟢",
                "completed": "✅",
                "failed": "❌",
                "killed": "⛔",
                "timeout": "⏰",
                "pending": "🟡",
            }.get(r["status"], "⚪")
            lines.append(
                f"\n{status_icon} <code>{aid_short}</code> <b>{r['role']}</b>"
                f"  <i>{r['status']}</i>  {mins}m{secs:02d}s"
            )
            lines.append(f"   └─ {goal_short}")
            # Row of action buttons for this agent
            row = [
                _btn("📋", "status", aid_short),
                _btn("📡", "events", aid_short),
            ]
            if r["status"] in ("running", "pending"):
                row.append(_btn("⏸️", "pause", aid_short))
            if r["status"] in ("running", "pending"):
                row.append(_btn("⛔", "kill", aid_short))
            if r["status"] in ("completed", "failed", "killed", "timeout"):
                row.append(_btn("▶️", "resume", aid_short))
            inline_keyboard.append(row)

        # Bottom row: refresh
        inline_keyboard.append([_btn("🔄 Actualizar", "refresh", "")])

        text = "\n".join(lines)
        reply_markup = {"inline_keyboard": inline_keyboard}

        if edit_msg_id:
            await self._tg.edit_message(chat_id, edit_msg_id, text)
            await self._tg.edit_message_reply_markup(chat_id, edit_msg_id, reply_markup)
        else:
            await self._tg.send_message(chat_id, text, reply_markup=reply_markup)

    async def _handle_agent_status(self, chat_id: int, agent_id_prefix: str) -> None:
        """Show detailed status for a single agent, matched by id prefix."""
        db_url = self._shared.lina_db_url
        if not db_url:
            await self._tg.send_message(chat_id, "⚠️ lina-db no disponible.")
            return
        try:
            import asyncpg

            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    "SELECT id, role, goal, status, pid, started_at, ended_at, "
                    "result_summary, created_at, "
                    "EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))::INT AS elapsed "
                    "FROM agent_sessions "
                    "WHERE id LIKE $1 || '%' ORDER BY created_at DESC LIMIT 1",
                    agent_id_prefix,
                )
                events_count = 0
                if rows:
                    events_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM agent_events WHERE session_id = $1",
                        rows[0]["id"],
                    )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            await self._tg.send_message(chat_id, f"⚠️ Error: <code>{exc}</code>")
            return

        if not rows:
            await self._tg.send_message(
                chat_id, f"ℹ️ No encontré agente con id <code>{agent_id_prefix}</code>"
            )
            return

        r = rows[0]
        elapsed = int(r["elapsed"] or 0)
        mins, secs = divmod(elapsed, 60)
        status_icon = {
            "running": "🔄",
            "pending": "⏳",
            "completed": "✅",
            "failed": "❌",
            "killed": "🛑",
            "timeout": "⌛",
        }.get(r["status"], "❓")
        summary = r["result_summary"] or "(sin resumen)"
        lines = [
            f"<b>Agente</b> <code>{r['id'][:12]}</code>",
            f"<b>Rol:</b> {r['role']}",
            f"<b>Estado:</b> {status_icon} {r['status']} ({mins}m{secs:02d}s)",
            f"<b>Eventos:</b> {events_count}",
            f"<b>Goal:</b> {r['goal'][:200]}",
            f"<b>Resumen:</b> {summary[:300]}",
        ]
        await self._tg.send_message(chat_id, "\n".join(lines))

    async def _handle_agent_events(self, chat_id: int, agent_id_prefix: str) -> None:
        """Show last 10 events for an agent, matched by id prefix."""
        db_url = self._shared.lina_db_url
        if not db_url:
            await self._tg.send_message(chat_id, "⚠️ lina-db no disponible.")
            return
        try:
            import asyncpg

            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                # Resolve full id from prefix
                full_id = await conn.fetchval(
                    "SELECT id FROM agent_sessions WHERE id LIKE $1 || '%' ORDER BY created_at DESC LIMIT 1",
                    agent_id_prefix,
                )
                if not full_id:
                    await self._tg.send_message(
                        chat_id, f"ℹ️ No encontré agente con id <code>{agent_id_prefix}</code>"
                    )
                    return
                rows = await conn.fetch(
                    "SELECT kind, ts, payload_json FROM agent_events "
                    "WHERE session_id = $1 ORDER BY ts DESC LIMIT 15",
                    full_id,
                )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            await self._tg.send_message(chat_id, f"⚠️ Error: <code>{exc}</code>")
            return

        if not rows:
            await self._tg.send_message(
                chat_id, f"ℹ️ Sin eventos para agente <code>{agent_id_prefix}</code>"
            )
            return

        lines = [f"<b>Últimos eventos — agente <code>{agent_id_prefix}</code>:</b>"]
        for r in reversed(rows):  # oldest first
            ts_str = r["ts"].strftime("%H:%M:%S") if r["ts"] else "?"
            payload = r["payload_json"] or {}
            detail = ""
            if isinstance(payload, dict):
                detail = payload.get("summary") or payload.get("text") or payload.get("error") or ""
            detail_str = f" — {str(detail)[:60]}" if detail else ""
            lines.append(f"  <code>{ts_str}</code> {r['kind']}{detail_str}")
        await self._tg.send_message(chat_id, "\n".join(lines))

    async def _wait_for_goosed(self, *, timeout: float = _GOOSED_RESTART_TIMEOUT) -> bool:
        """Poll until goosed responds to /status or timeout expires. Returns True if recovered."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self._goosed.is_alive():
                return True
            await asyncio.sleep(3.0)
        return False

    def set_observer(self, observer: ObserveServer) -> None:
        """Set observer for live streaming events to WebSocket clients."""
        self._observer = observer

    async def _reply(
        self,
        chat_id: int,
        user_msg_id: int,
        text: str,
        cancel_event: asyncio.Event,
        *,
        _retry: bool = False,
        is_comm_msg: bool = False,
    ) -> None:
        session_id = self._session_id(chat_id)
        reply_start = time.monotonic()
        first_send_ts: float | None = None  # timestamp of the first message sent to the user

        # Ensure the session exists in goosed (creates it if needed)
        session_is_new = False
        try:
            session_id, session_is_new = await self._goosed.ensure_session(session_id)
            self._sessions[chat_id] = session_id
            if self._observer:
                self._observer.push_event(session_id, "user_message", text)
        except Exception as exc:
            logger.warning("Could not ensure session for chat %s: %s", chat_id, exc)

        # First message to this chat in this process lifetime: inject previous context
        # Comm bridge: NO inyectar contexto de sesiones previas (contiene @mentions basura)
        if chat_id not in self._sessions_initialized and not is_comm_msg:
            self._sessions_initialized.add(chat_id)
            if self._shared.lina_db_url and session_is_new:
                await self._maybe_inject_context(chat_id, session_id, cancel_event)

        # Accumulators for the current turn
        thinking_acc = ""
        body_acc = ""
        # Cumulative thinking across all tool-call cycles in this turn (never reset).
        # Used for reasoning trace persistence (issue #62).
        total_thinking_acc = ""
        finish_token_state = None  # populated from Finish event token_state

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
                    if self._observer:
                        self._observer.push_event(session_id, "error", event.error)
                    await _seal_all()
                    if not is_comm_msg:
                        await self._tg.send_message(chat_id, f"⚠️ Error: <code>{event.error}</code>")
                    return

                if event.event_type == EventType.FINISH:
                    if self._observer:
                        self._observer.push_event(session_id, "finish", event)
                    finish_token_state = event.token_state
                    break

                if event.event_type != EventType.MESSAGE:
                    continue

                logger.info(
                    "SSE_EVENT: %s types=%s",
                    event.event_type,
                    [i.content_type for i in event.contents],
                )
                # Process content items
                for item in event.contents:
                    logger.debug(
                        "CONTENT_TYPE: %s (tool_name=%s) (text=%s)",
                        item.content_type,
                        getattr(item, "tool_name", ""),
                        item.text[:30] if item.text else "",
                    )
                    if item.content_type == "thinking":
                        if self._observer:
                            self._observer.push_event(session_id, "thinking", item.thinking)
                        thinking_acc += item.thinking
                        total_thinking_acc += item.thinking
                        if thinking_bubble_msg_id is None:
                            # Open the thinking bubble (its own Telegram message)
                            html = format_with_thinking(thinking_acc, "", False)
                            thinking_bubble_msg_id = await self._tg.send_message(chat_id, html)
                            if first_send_ts is None:
                                first_send_ts = time.monotonic()
                            thinking_bubble = StreamingBubble(
                                tick=self._shared.pacer_tick,
                                edit_fn=_edit_thinking_bubble,
                            )
                            thinking_bubble.start()
                            thinking_bubble.update(thinking=thinking_acc, body="")
                        else:
                            if thinking_bubble:
                                thinking_bubble.update(thinking=thinking_acc, body="")

                    elif item.content_type == "text":
                        if self._observer:
                            self._observer.push_event(session_id, "text", item.text)
                        body_acc += item.text
                        import logging as __lg
                        __lg.getLogger(__name__).info("COMM_CHATID: chat_id=%s target=%s eq=%s", chat_id, -5110614353, chat_id == -5110614353)
                        if chat_id == -5110614353 and not is_comm_msg:
                            # Mensaje de USUARIO REAL en grupo Comm: relay via Comm account
                            if body_bubble_msg_id is None and body_acc.strip():
                                asyncio.create_task(
                                    self._send_to_group(body_acc.strip()),
                                    name=f"comm-text-{chat_id}",
                                )
                            if thinking_bubble:
                                await thinking_bubble.seal()
                                thinking_bubble = None
                            if body_bubble_msg_id is None:
                                body_bubble_msg_id = -1  # marcado como "manejado por Comm"
                        elif chat_id == -5110614353 and is_comm_msg:
                            # Comm bridge: NO publicar en el grupo (la respuesta va por DB)
                            if thinking_bubble:
                                await thinking_bubble.seal()
                                thinking_bubble = None
                            body_bubble_msg_id = -1  # marcado como "manejado por Comm"
                        elif body_bubble_msg_id is None:
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
                                tick=self._shared.pacer_tick,
                                edit_fn=_edit_body_bubble,
                            )
                            body_bubble.start()
                            body_bubble.update(thinking="", body=body_acc)
                        else:
                            if body_bubble:
                                body_bubble.update(thinking="", body=body_acc)

                    elif item.content_type == "tool_request":
                        logger.info(
                            "TOOL_REQUEST: %s args=%s",
                            item.tool_name,
                            item.args_preview[:80] if item.args_preview else "",
                        )
                        if self._observer:
                            self._observer.push_event(session_id, "tool_request", item)
                        # Seal all active bubbles before showing tool status
                        await _seal_all()

                        html = format_tool_status(
                            item.tool_name, item.args_preview, False, None, ""
                        )
                        if not is_comm_msg:
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
                        logger.info(
                            "TOOL_RESPONSE: %s success=%s result=%s",
                            item.tool_name if hasattr(item, "tool_name") else "",
                            item.success if hasattr(item, "success") else "?",
                            item.result_preview[:80] if item.result_preview else "",
                        )
                        if self._observer:
                            self._observer.push_event(session_id, "tool_response", item)
                        # Update the matching tool status card
                        tool_entry = active_tools.pop(item.tool_name, None)
                        if tool_entry and not is_comm_msg:
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
            if is_comm_msg:
                # Comm bridge: no mostrar errores en el grupo; reintentar silenciosamente
                if not _retry:
                    self._sessions.pop(chat_id, None)
                    await asyncio.sleep(1.0)
                    await self._reply(chat_id, user_msg_id, text, cancel_event, _retry=True)
                return
            if _retry:
                # Already retried once — give up gracefully
                await self._tg.send_message(
                    chat_id, f"⚠️ {self._name} no está disponible. Intentá de nuevo."
                )
                return
            status_id = await self._tg.send_message(
                chat_id, f"⏳ {self._name} se está reiniciando, un momento..."
            )
            recovered = await self._wait_for_goosed()
            if not recovered:
                await self._tg.edit_message(
                    chat_id,
                    status_id,
                    f"⚠️ {self._name} no está disponible. Intentá de nuevo.",
                )
                return
            await self._tg.edit_message(
                chat_id, status_id, f"✅ {self._name} de vuelta. Reprocesando..."
            )
            # Invalidate cached session — force ensure_session to create/resume fresh
            self._sessions.pop(chat_id, None)
            await asyncio.sleep(1.0)  # let goosed settle
            await self._reply(chat_id, user_msg_id, text, cancel_event, _retry=True)
            return
        except Exception as exc:
            logger.exception("Unexpected error during reply: %s", exc)
            await _seal_all()
            if not is_comm_msg:
                # Expose only the error type, not the full message (may contain internals)
                await self._tg.send_message(
                    chat_id, f"⚠️ Error inesperado (<code>{type(exc).__name__}</code>). Revisá los logs."
                )
            return

        # Final seal of whichever bubble is still active
        try:
            await _seal_all()
        except Exception as exc:
            logger.warning("_seal_all failed (Telegram error, non-fatal): %s", exc)

        total_s = time.monotonic() - reply_start
        ttft_s = (first_send_ts - reply_start) if first_send_ts else total_s
        logger.info(
            "reply: chat=%s ttft=%.2fs total=%.2fs",
            chat_id,
            ttft_s,
            total_s,
        )

        # ── HumanInputRequest: attach approval buttons if LINA asks ──
        if body_acc and body_bubble_msg_id is not None and body_bubble_msg_id > 0:
            await self._maybe_attach_approval_buttons(
                chat_id, body_bubble_msg_id, body_acc, session_id, text
            )

        # Belt-and-suspenders: body arrived but no bubble was created somehow
        if not is_comm_msg and body_acc and body_bubble_msg_id is None and thinking_bubble_msg_id is None:
            await self._tg.send_message(chat_id, markdown_to_telegram_html(body_acc))

        # Save response for /voz command
        if body_acc:
            self._last_response[chat_id] = body_acc

        # Persist this turn for session recovery across restarts (best-effort)
        if self._shared.lina_db_url and text.strip() and body_acc.strip():
            asyncio.create_task(
                self._persist_turn(session_id, text, body_acc, finish_token_state),
                name=f"persist-turn-{chat_id}",
            )
        # Persist reasoning trace if thinking content was produced (best-effort, issue #62)
        if self._shared.lina_db_url and total_thinking_acc.strip():
            import hashlib

            prompt_hash = hashlib.sha256(text.encode()).hexdigest()
            asyncio.create_task(
                self._persist_trace(session_id, total_thinking_acc, prompt_hash),
                name=f"persist-trace-{chat_id}",
            )

    # ─── Session persistence helpers ─────────────────────────────────────────

    async def _send_to_group(self, text: str) -> None:
        """Send a message to the Comm group via Telethon (Comm phone relay).

        This makes the message appear as a USER message (from Comm), not a bot message.
        Other bots can detect @mentions in this message.
        No @mention is added (this is a response, not a call to another bot).
        """
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetDialogsRequest
            from telethon.tl.types import InputPeerEmpty

            session_path = "/home/fede/lina/comm/comm_session.session"
            api_id = 35434942
            api_hash = "9f2a614fbf2e8cbfaf844561b7f43294"

            client = TelegramClient(session_path, api_id, api_hash)
            client.parse_mode = None
            await client.start()

            dialogs = await client(GetDialogsRequest(
                offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
                limit=200, hash=0,
            ))
            gid = None
            for d in dialogs.chats:
                title = getattr(d, "title", "") or ""
                if "Comm" in title:
                    gid = d.id
                    break

            if gid:
                await client.send_message(gid, text)
                logger.info("Comm reply sent to group (user message, %d chars)", len(text))
            else:
                logger.warning("Comm group not found for _send_to_group")

            await client.disconnect()
        except Exception as exc:
            logger.warning("_send_to_group failed: %s", exc)

    async def _persist_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        token_state: TokenState | None = None,
    ) -> None:
        """Save a user+assistant turn to PostgreSQL. Silently swallows errors."""
        db_url = self._shared.lina_db_url
        if not db_url:
            return
        try:
            await save_message(db_url, session_id, "user", user_text)
            await save_message(db_url, session_id, "assistant", assistant_text)
            if token_state is not None:
                await save_token_usage(
                    db_url,
                    session_id,
                    input_tokens=token_state.input_tokens,
                    output_tokens=token_state.output_tokens,
                    accumulated_cost_usd=token_state.accumulated_cost,
                )
        except Exception as exc:
            logger.debug("_persist_turn failed for session %s: %s", session_id, exc)

    async def _persist_trace(
        self,
        session_id: str,
        thinking_text: str,
        prompt_hash: str | None = None,
    ) -> None:
        """Persist the <think> block for this turn. Silently swallows errors (issue #62)."""
        db_url = self._shared.lina_db_url
        if not db_url:
            return
        try:
            await save_trace(db_url, session_id, thinking_text, prompt_hash)
        except Exception as exc:
            logger.debug("_persist_trace failed for session %s: %s", session_id, exc)

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
        db_url = self._shared.lina_db_url
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
            # poll() returns (update_id, item) tuples
            if isinstance(update, tuple):
                uid, item = update
                offset = uid + 1
                if isinstance(item, TelegramCallbackQuery):
                    asyncio.create_task(self._handle_callback(item))
                else:
                    asyncio.create_task(self._handle(item))
            else:
                # Legacy: object with .update_id, .message, .callback_query
                offset = update.update_id + 1
                if hasattr(update, "message") and update.message:
                    asyncio.create_task(self._handle(update.message))
                if hasattr(update, "callback_query") and update.callback_query:
                    asyncio.create_task(self._handle_callback(update.callback_query))
        # Process comm messages (HTTP bridge from other bots)
        if self._observer:
            for cmd in self._observer.pop_comm_messages():
                logger.info(
                    "Comm: handling from=%s text=%.60s", cmd.get("from", "?"), cmd.get("text", "")
                )
                asyncio.create_task(
                    self._handle(
                        TelegramMessage(
                            message_id=int(time.time() * 1000) % (2**31),
                            chat=TelegramChat(id=-5110614353, chat_type="group"),
                            text=cmd.get("text", ""),
                            entities=[{"offset": 0, "length": 0, "type": "mention"}],
                            from_user=TelegramUser(
                                id=8887121852, first_name="Comm", is_bot=True, username="comm_bot"
                            ),
                            voice=None,
                        )
                    ),
                    name=f"comm-{cmd.get('id', '')}-{int(time.time())}",
                )
        return offset

    async def _handle_callback(self, cq: TelegramCallbackQuery) -> None:
        """Handle an inline keyboard button press."""
        data = cq.data
        parts = data.split(":", 2)
        action = parts[0] if len(parts) >= 1 else ""
        agent_id = parts[1] if len(parts) >= 2 else ""
        logger.info("Callback: action=%s agent=%s chat=%s", action, agent_id, cq.chat_id)

        if action == "refresh":
            # Refresh the dashboard
            await self._handle_agents(cq.chat_id, edit_msg_id=cq.message_id)
            return

        await self._tg.answer_callback_query(cq.id)

        if action == "status" and agent_id:
            await self._handle_agent_status(cq.chat_id, agent_id)
        elif action == "events" and agent_id:
            await self._handle_agent_events(cq.chat_id, agent_id)
        elif action == "kill" and agent_id:
            await self._kill_agent(cq.chat_id, agent_id)
        elif action == "pause" and agent_id:
            await self._pause_agent(cq.chat_id, agent_id)
        elif action == "resume" and agent_id:
            await self._resume_agent(cq.chat_id, agent_id)
        elif action == "approve":
            # User approved a human input request — send approval as follow-up
            await self._tg.answer_callback_query(cq.id, "✅ Aprobado. Enviando respuesta...")
            await self._tg.send_message(cq.chat_id, f"✅ Aprobado — reenviando a {self._name}…")
            cancel = self._cancels.get(cq.chat_id) or asyncio.Event()
            # Send "Sí, aprobado" as a new user message to LINA
            asyncio.create_task(
                self._reply(cq.chat_id, None, "Sí, aprobado ✅", cancel),
                name=f"approve-{cq.chat_id}",
            )
            return
        elif action == "modify":
            # User wants to type their own response
            await self._tg.answer_callback_query(cq.id, "✏️ Escribí tu respuesta...")
            await self._tg.send_message(
                cq.chat_id, "✏️ Escribí tu modificación o instrucción directamente como mensaje:"
            )
            return
        elif action == "reject":
            # User rejected — send rejection as follow-up
            await self._tg.answer_callback_query(cq.id, "❌ Rechazado.")
            await self._tg.send_message(cq.chat_id, f"❌ Rechazado — reenviando a {self._name}…")
            cancel = self._cancels.get(cq.chat_id) or asyncio.Event()
            asyncio.create_task(
                self._reply(cq.chat_id, None, "No, no aprobado ❌", cancel),
                name=f"reject-{cq.chat_id}",
            )
            return
        else:
            logger.warning("Unknown callback: %s", data)

    async def run(self) -> None:
        """Main loop: poll forever with heartbeat and circuit breaker."""
        logger.info("lina-gateway starting (goosed=%s)", self._cfg.goosed_url)

        # Start heartbeat
        if self._heartbeat:
            await self._heartbeat.start()

        offset: int | None = None
        retry_delay = 1.0
        try:
            while True:
                # Circuit breaker check
                if self._breaker and await self._breaker.is_open():
                    logger.warning("%s: circuit open, skipping poll", self._name)
                    await asyncio.sleep(30)
                    continue

                try:
                    offset = await self.run_once(offset)
                    retry_delay = 1.0
                    # Record success on connection
                    if self._breaker:
                        await self._breaker.record_success()
                except Exception as exc:
                    # Record failure
                    if self._breaker:
                        await self._breaker.record_failure()
                    logger.error("Poll error (retry in %.0fs): %s", retry_delay, exc)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60.0)
        finally:
            # Stop heartbeat
            if self._heartbeat:
                await self._heartbeat.stop()

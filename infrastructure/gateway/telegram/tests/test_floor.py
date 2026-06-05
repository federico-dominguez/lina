"""Unit tests for FloorTokenManager."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lina_gateway.floor import FloorTokenManager, FloorToken


@pytest.fixture
def floor():
    """FloorTokenManager sin DB (modo bypass)."""
    return FloorTokenManager(db_url=None)


class TestFloorTokenBypass:
    """Tests en modo bypass (sin DB)."""

    def test_bypass_acquire(self, floor):
        """Sin DB, try_acquire siempre concede el token."""
        token = asyncio.run(
            floor.try_acquire("lina", "test-conv", timeout=30.0)
        )
        assert token.granted is True
        assert token.conversation_id == "test-conv"
        assert token.reason == ""  # bypass mode returns empty reason

    def test_bypass_release_no_error(self, floor):
        """Sin DB, release es no-op."""
        asyncio.run(floor.release(1, "test-conv", "lina", "voluntary"))

    def test_bypass_enqueue_no_error(self, floor):
        """Sin DB, enqueue_message retorna None."""
        msg_id = asyncio.run(
            floor.enqueue_message("test-conv", "lina", "goose", "hola")
        )
        assert msg_id is None

    def test_bypass_context_empty(self, floor):
        """Sin DB, get_context_messages retorna vacío."""
        msgs = asyncio.run(
            floor.get_context_messages("test-conv", limit=5)
        )
        assert msgs == []

    def test_bypass_active_floor_none(self, floor):
        """Sin DB, get_active_floor retorna None."""
        result = asyncio.run(floor.get_active_floor("test-conv"))
        assert result is None

    def test_bypass_pending_empty(self, floor):
        """Sin DB, get_pending_messages retorna vacío."""
        msgs = asyncio.run(
            floor.get_pending_messages("test-conv", "lina")
        )
        assert msgs == []

    def test_is_enabled_false(self, floor):
        """Sin DB, is_enabled es False."""
        assert floor.is_enabled is False

    def test_bypass_token_data(self, floor):
        """Sin DB con conversation_id=None, genera UUID."""
        token = asyncio.run(floor.try_acquire("lina", timeout=15.0))
        assert token.granted is True
        assert token.conversation_id != ""


class TestFloorTokenDataclass:
    """Tests del dataclass FloorToken."""

    def test_default_values(self):
        """FloorToken con valores default."""
        t = FloorToken(granted=True, conversation_id="abc")
        assert t.granted is True
        assert t.conversation_id == "abc"
        assert t.active_bot is None
        assert t.reason == ""
        assert t.token_id == 0

    def test_all_fields(self):
        """FloorToken con todos los campos."""
        t = FloorToken(
            granted=False,
            conversation_id="conv-1",
            active_bot="goose",
            reason="busy",
            token_id=42,
        )
        assert t.granted is False
        assert t.active_bot == "goose"
        assert t.reason == "busy"
        assert t.token_id == 42


class TestContextMessage:
    """Tests del dataclass ContextMessage."""

    def test_creation(self):
        from lina_gateway.floor import ContextMessage

        cm = ContextMessage(
            from_bot="lina",
            to_bot="goose",
            message="hola",
            created_at="2026-01-01T00:00:00",
        )
        assert cm.from_bot == "lina"
        assert cm.to_bot == "goose"
        assert cm.message == "hola"

    def test_context_message_defaults(self):
        from lina_gateway.floor import ContextMessage

        cm = ContextMessage(from_bot="x", to_bot="y", message="msg", created_at="")
        assert cm.created_at == ""


class TestDeterministicUuid:
    """UUID determinista debe generar el mismo conv_id para el mismo chat_id."""

    def test_same_chat_same_uuid(self):
        chat_id = -5110614353
        conv1 = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-chat-{chat_id}"))
        conv2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-chat-{chat_id}"))
        assert conv1 == conv2

    def test_different_chat_different_uuid(self):
        conv1 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "telegram-chat-111"))
        conv2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "telegram-chat-222"))
        assert conv1 != conv2

    def test_valid_uuid_format(self):
        chat_id = 123456789
        conv = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-chat-{chat_id}"))
        # Debe ser UUID válido
        parsed = uuid.UUID(conv)
        assert str(parsed) == conv


class TestFloorWithMockDB:
    """Tests del FloorTokenManager con asyncpg mockeado.

    Cada test mockea asyncpg.connect() y configura el objeto connection
    devuelto con los valores esperados para cada escenario.
    """

    @pytest.fixture
    def mock_db(self):
        """Crea un mock de asyncpg.connect() y devuelve la conexión mockeada."""
        conn = AsyncMock()
        conn.close = AsyncMock()
        mock_connect = AsyncMock(return_value=conn)
        with patch("asyncpg.connect", mock_connect):
            yield conn

    @pytest.fixture
    def floor_db(self, mock_db):
        """FloorTokenManager con DB mockeada."""
        return FloorTokenManager(db_url="postgresql://fake:5432/lina")

    # ── Helpers para crear valores datetime mock ─────────────────

    def _future(self):
        """Return a future datetime (2126)."""
        from datetime import datetime, timezone
        return datetime(2126, 1, 1, tzinfo=timezone.utc)

    def _past(self):
        """Return a past datetime (2020)."""
        from datetime import datetime, timezone
        return datetime(2020, 1, 1, tzinfo=timezone.utc)

    # ── Tests ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_acquire_no_active_floor(self, floor_db, mock_db):
        """Sin floor activo, try_acquire concede el token."""
        mock_db.fetchrow = AsyncMock(return_value=None)  # No hay floor activo
        mock_db.fetchval = AsyncMock(return_value=1)     # Nuevo floor id=1
        token = await floor_db.try_acquire("lina", "conv-1", timeout=30.0)
        assert token.granted is True
        assert token.conversation_id == "conv-1"
        assert token.reason == "ok"
        assert token.token_id == 1
        mock_db.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_acquire_same_bot_renews(self, floor_db, mock_db):
        """Si el mismo bot ya tiene el floor, se renueva el timeout."""
        mock_db.fetchrow = AsyncMock(return_value={
            "id": 1, "active_bot": "lina", "expires_at": self._future()
        })
        mock_db.fetchval = AsyncMock(return_value=None)  # No INSERT
        token = await floor_db.try_acquire("lina", "conv-1", timeout=30.0)
        assert token.granted is True
        assert token.reason == "renewed"
        assert token.token_id == 1
        mock_db.execute.assert_awaited_once()  # UPDATE para renovar

    @pytest.mark.asyncio
    async def test_acquire_other_bot_denied(self, floor_db, mock_db):
        """Si otro bot tiene el floor activo, se deniega el turno."""
        mock_db.fetchrow = AsyncMock(return_value={
            "id": 1, "active_bot": "goose", "expires_at": self._future()
        })
        token = await floor_db.try_acquire("lina", "conv-1", timeout=30.0)
        assert token.granted is False
        assert token.reason == "busy"
        assert token.active_bot == "goose"

    @pytest.mark.asyncio
    async def test_acquire_timeout_reassigns(self, floor_db, mock_db):
        """Si el floor expiró, se reasigna al bot que pide."""
        mock_db.fetchrow = AsyncMock(return_value={
            "id": 1, "active_bot": "goose", "expires_at": self._past()
        })
        mock_db.fetchval = AsyncMock(return_value=99)  # Nuevo floor id
        token = await floor_db.try_acquire("lina", "conv-1", timeout=30.0)
        assert token.granted is True
        assert token.reason == "timeout_reassigned"
        assert token.token_id == 99
        # Debe haber hecho UPDATE release + INSERT nuevo
        assert mock_db.execute.await_count >= 1
        assert mock_db.fetchval.await_count >= 1

    @pytest.mark.asyncio
    async def test_release_voluntary(self, floor_db, mock_db):
        """Liberación voluntaria del token."""
        mock_db.execute = AsyncMock(return_value="UPDATE 1")
        await floor_db.release(1, "conv-1", "lina", reason="voluntary")
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enqueue_and_ack(self, floor_db, mock_db):
        """Encolar mensaje y marcarlo como procesado."""
        mock_db.fetchval = AsyncMock(return_value=42)
        msg_id = await floor_db.enqueue_message("conv-1", "lina", "goose", "hola")
        assert msg_id == 42

        # Ack
        mock_db.execute.reset_mock()
        mock_db.execute = AsyncMock()
        await floor_db.ack_message(42)
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_pending_messages(self, floor_db, mock_db):
        """Obtener mensajes pendientes para un bot."""
        mock_db.fetch = AsyncMock(return_value=[
            {"id": 1, "from_bot": "goose", "message": "msg1", "created_at": self._future()},
            {"id": 2, "from_bot": "goose", "message": "msg2", "created_at": self._future()},
        ])
        msgs = await floor_db.get_pending_messages("conv-1", "lina")
        assert len(msgs) == 2
        assert msgs[0]["from_bot"] == "goose"

    @pytest.mark.asyncio
    async def test_context_messages(self, floor_db, mock_db):
        """Obtener contexto acumulativo de la conversación."""
        mock_db.fetch = AsyncMock(return_value=[
            {"from_bot": "lina", "to_bot": "goose", "message": "ping", "created_at": self._future()},
            {"from_bot": "goose", "to_bot": "lina", "message": "pong", "created_at": self._future()},
        ])
        msgs = await floor_db.get_context_messages("conv-1", limit=5)
        assert len(msgs) == 2
        assert msgs[0].from_bot == "goose"
        assert msgs[1].to_bot == "goose"

    @pytest.mark.asyncio
    async def test_get_active_floor(self, floor_db, mock_db):
        """Obtener floor activo."""
        mock_db.fetchrow = AsyncMock(return_value={
            "id": 1, "active_bot": "lina",
            "acquired_at": self._past(), "expires_at": self._future(),
        })
        result = await floor_db.get_active_floor("conv-1")
        assert result is not None
        assert result["active_bot"] == "lina"
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_db_error_fallback(self, floor_db, mock_db):
        """Si la DB falla, concede el token como fallback."""
        mock_db.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        token = await floor_db.try_acquire("lina", "conv-1", timeout=30.0)
        assert token.granted is True  # fallback: concede igual
        assert "error_fallback" in token.reason

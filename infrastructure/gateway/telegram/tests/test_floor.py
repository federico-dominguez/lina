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

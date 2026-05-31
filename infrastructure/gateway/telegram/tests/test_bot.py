"""Unit tests for lina_gateway.bot — hot-swap / goosed-restart handling (issue #57)
and smart context injection (issue #60)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lina_gateway.boot_hook import SmartContext
from lina_gateway.bot import _GOOSED_TRANSPORT_ERRORS, Bot
from lina_gateway.config import Config

# ─── Test doubles ─────────────────────────────────────────────────────────────


def _make_config() -> Config:
    """Minimal Config with env vars mocked."""
    with patch.dict(
        "os.environ",
        {
            "TELEGRAM_BOT_TOKEN": "123:TEST",
            "GOOSED_URL": "https://goosed:3000",
            "GOOSE_SERVER_SECRET_KEY": "secret",
            "GOOSE_GATEWAY_TRUSTED_USERS": "telegram:9999",
        },
        clear=False,
    ):
        return Config()


def _make_bot() -> Bot:
    return Bot(_make_config())


class FakeTg:
    """Records send/edit calls for assertions."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self._next_id = 100

    async def send_message(self, chat_id: int, html: str) -> int:
        self._next_id += 1
        self.sent.append((chat_id, html))
        return self._next_id

    async def edit_message(self, chat_id: int, message_id: int, html: str) -> bool:
        self.edited.append((chat_id, message_id, html))
        return True

    async def send_chat_action(self, *_: object) -> None:
        pass

    async def set_reaction(self, *_: object) -> None:
        pass


# ─── _GOOSED_TRANSPORT_ERRORS ─────────────────────────────────────────────────


class TestTransportErrorConstant:
    def test_contains_expected_httpx_errors(self) -> None:
        assert httpx.RemoteProtocolError in _GOOSED_TRANSPORT_ERRORS
        assert httpx.ConnectError in _GOOSED_TRANSPORT_ERRORS
        assert httpx.ReadError in _GOOSED_TRANSPORT_ERRORS
        assert httpx.WriteError in _GOOSED_TRANSPORT_ERRORS

    def test_does_not_contain_value_error(self) -> None:
        assert ValueError not in _GOOSED_TRANSPORT_ERRORS


# ─── _wait_for_goosed ─────────────────────────────────────────────────────────


class TestWaitForGoosed:
    @pytest.mark.asyncio
    async def test_returns_true_when_alive_on_first_poll(self) -> None:
        bot = _make_bot()
        bot._goosed.is_alive = AsyncMock(return_value=True)
        result = await bot._wait_for_goosed(timeout=10.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_never_alive(self) -> None:
        bot = _make_bot()
        bot._goosed.is_alive = AsyncMock(return_value=False)
        # Use very short timeout to not slow the test suite
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await bot._wait_for_goosed(timeout=0.01)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_after_second_poll(self) -> None:
        bot = _make_bot()
        responses = [False, True]
        bot._goosed.is_alive = AsyncMock(side_effect=responses)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await bot._wait_for_goosed(timeout=30.0)
        assert result is True


# ─── transport error NOT exposed to user ─────────────────────────────────────


class TestTransportErrorNotExposedToUser:
    """When goosed drops the connection, the raw error must never reach Telegram."""

    @pytest.mark.asyncio
    async def test_remote_protocol_error_shows_restarting_message(self) -> None:
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._sessions[9999] = "telegram-9999"

        # ensure_session succeeds, reply_stream raises transport error
        bot._goosed.ensure_session = AsyncMock(return_value="telegram-9999")

        async def _failing_stream(*_: object):  # type: ignore[override]
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body",
                request=MagicMock(),
            )
            yield  # make it an async generator

        bot._goosed.reply_stream = _failing_stream  # type: ignore[assignment]

        # _wait_for_goosed returns False immediately (goosed stays down)
        bot._wait_for_goosed = AsyncMock(return_value=False)

        cancel = asyncio.Event()
        await bot._reply(9999, 1, "test message", cancel)

        # Must have sent exactly ONE "restarting" message — not the raw error
        assert len(fake_tg.sent) == 1
        _chat_id, text = fake_tg.sent[0]
        assert "reiniciando" in text.lower()
        # Raw error MUST NOT appear in Telegram
        assert "peer closed" not in text
        assert "incomplete chunked" not in text

    @pytest.mark.asyncio
    async def test_connect_error_triggers_restarting_message(self) -> None:
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._sessions[9999] = "telegram-9999"

        bot._goosed.ensure_session = AsyncMock(return_value="telegram-9999")

        async def _failing_stream(*_: object):  # type: ignore[override]
            raise httpx.ConnectError("connection refused", request=MagicMock())
            yield

        bot._goosed.reply_stream = _failing_stream  # type: ignore[assignment]
        bot._wait_for_goosed = AsyncMock(return_value=False)

        cancel = asyncio.Event()
        await bot._reply(9999, 1, "test message", cancel)

        assert len(fake_tg.sent) == 1
        assert "reiniciando" in fake_tg.sent[0][1].lower()


# ─── retry after recovery ─────────────────────────────────────────────────────


class TestGoosedRestartRetry:
    @pytest.mark.asyncio
    async def test_retries_and_succeeds_after_restart(self) -> None:
        """When goosed recovers, the message is retried and succeeds."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._sessions[9999] = "telegram-9999"

        bot._goosed.ensure_session = AsyncMock(return_value="telegram-9999")

        call_count = 0

        async def _stream_first_fails_then_ok(session_id: str, text: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.RemoteProtocolError("peer closed", request=MagicMock())
            from lina_gateway.goose_client import EventType, MessageContent, MessageEvent

            yield MessageEvent(
                event_type=EventType.MESSAGE,
                role="assistant",
                contents=[MessageContent(content_type="text", text="Hola!")],
            )
            yield MessageEvent(event_type=EventType.FINISH, finish_reason="stop")

        bot._goosed.reply_stream = _stream_first_fails_then_ok  # type: ignore[assignment]
        bot._wait_for_goosed = AsyncMock(return_value=True)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            cancel = asyncio.Event()
            await bot._reply(9999, 1, "hola", cancel)

        assert call_count == 2  # first attempt failed, second succeeded
        # Status message was sent and then edited
        restarting_msgs = [t for _, t in fake_tg.sent if "reiniciando" in t.lower()]
        assert len(restarting_msgs) == 1
        recovered_edits = [t for _, _, t in fake_tg.edited if "vuelta" in t.lower()]
        assert len(recovered_edits) == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_again_when_already_retried(self) -> None:
        """_retry=True prevents infinite loops."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._sessions[9999] = "telegram-9999"

        bot._goosed.ensure_session = AsyncMock(return_value="telegram-9999")

        async def _always_fails(*_: object):
            raise httpx.RemoteProtocolError("peer closed", request=MagicMock())
            yield

        bot._goosed.reply_stream = _always_fails  # type: ignore[assignment]

        cancel = asyncio.Event()
        # Call with _retry=True — should NOT call _wait_for_goosed again
        await bot._reply(9999, 1, "hola", cancel, _retry=True)

        assert len(fake_tg.sent) == 1
        assert "no está disponible" in fake_tg.sent[0][1].lower()


# ─── unexpected errors still surface (but safely) ─────────────────────────────


class TestUnexpectedErrorHandling:
    @pytest.mark.asyncio
    async def test_unexpected_error_shows_type_not_message(self) -> None:
        """ValueError and similar unexpected errors show only the type name, not raw message."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._sessions[9999] = "telegram-9999"

        bot._goosed.ensure_session = AsyncMock(return_value="telegram-9999")

        async def _bad_stream(*_: object):
            raise ValueError("sensitive internal detail that must not leak")
            yield

        bot._goosed.reply_stream = _bad_stream  # type: ignore[assignment]

        cancel = asyncio.Event()
        await bot._reply(9999, 1, "test", cancel)

        assert len(fake_tg.sent) == 1
        _, text = fake_tg.sent[0]
        # Shows type name
        assert "ValueError" in text
        # But NOT the raw message
        assert "sensitive internal detail" not in text


# ─── Smart context injection (issue #60) ─────────────────────────────────────


class TestSmartContextInjection:
    """_maybe_inject_context uses get_smart_context (issue #60)."""

    @pytest.mark.asyncio
    async def test_skips_injection_when_no_data(self) -> None:
        """If SmartContext.has_data is False, nothing is sent to Telegram."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]

        empty_ctx = SmartContext(summary="", messages=[])
        with patch(
            "lina_gateway.bot.get_smart_context",
            new=AsyncMock(return_value=empty_ctx),
        ):
            cancel = asyncio.Event()
            await bot._maybe_inject_context(9999, "telegram-9999", cancel)

        assert fake_tg.sent == []

    @pytest.mark.asyncio
    async def test_sends_status_message_when_context_exists(self) -> None:
        """If SmartContext has data, gateway sends '📚 Recuperando...' message."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._cfg.lina_db_url = "postgresql://fake"  # ensure injection is not skipped

        ctx = SmartContext(summary="Resumen previo.", messages=[])

        async def _silent_stream(*_: object):
            return
            yield  # make it an async generator

        bot._goosed.reply_stream = _silent_stream  # type: ignore[assignment]

        with patch(
            "lina_gateway.bot.get_smart_context",
            new=AsyncMock(return_value=ctx),
        ):
            cancel = asyncio.Event()
            await bot._maybe_inject_context(9999, "telegram-9999", cancel)

        assert len(fake_tg.sent) == 1
        _, text = fake_tg.sent[0]
        assert "📚" in text
        assert "contexto" in text.lower()

    @pytest.mark.asyncio
    async def test_edits_status_to_recovered_after_warmup(self) -> None:
        """After warmup stream completes, the status message is edited."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._cfg.lina_db_url = "postgresql://fake"  # ensure injection is not skipped

        ctx = SmartContext(summary="Algo.", messages=[])

        async def _silent_stream(*_: object):
            return
            yield

        bot._goosed.reply_stream = _silent_stream  # type: ignore[assignment]

        with patch(
            "lina_gateway.bot.get_smart_context",
            new=AsyncMock(return_value=ctx),
        ):
            cancel = asyncio.Event()
            await bot._maybe_inject_context(9999, "telegram-9999", cancel)

        assert len(fake_tg.edited) == 1
        _, _, edited_text = fake_tg.edited[0]
        assert "recuperado" in edited_text.lower()

    @pytest.mark.asyncio
    async def test_no_db_url_skips_entirely(self) -> None:
        """Without lina_db_url, injection is skipped with no DB call."""
        bot = _make_bot()
        fake_tg = FakeTg()
        bot._tg = fake_tg  # type: ignore[assignment]
        bot._cfg = MagicMock()
        bot._cfg.lina_db_url = None

        with patch("lina_gateway.bot.get_smart_context") as mock_gsc:
            cancel = asyncio.Event()
            await bot._maybe_inject_context(9999, "telegram-9999", cancel)

        mock_gsc.assert_not_called()
        assert fake_tg.sent == []

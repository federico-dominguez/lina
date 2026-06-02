"""Unit tests for agent_notifier.py and new bot commands (/agents, /instruct)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lina_gateway.agent_notifier import AgentNotifier, _format_notification

# ─── _format_notification ─────────────────────────────────────────────────────


class TestFormatNotification:
    def test_completed_with_summary(self) -> None:
        row = {
            "id": "abcdef1234567890",
            "role": "dev",
            "goal": "crear issue en GitHub",
            "status": "completed",
            "result_summary": "Issue #106 creado correctamente",
        }
        msg = _format_notification(row)
        assert "✅" in msg
        assert "completó" in msg
        assert "dev" in msg
        assert "<code>abcdef12…</code>" in msg
        assert "crear issue en GitHub" in msg
        assert "Issue #106 creado correctamente" in msg

    def test_failed_without_summary(self) -> None:
        row = {
            "id": "deadbeef12345678",
            "role": "tester",
            "goal": "correr tests",
            "status": "failed",
            "result_summary": None,
        }
        msg = _format_notification(row)
        assert "❌" in msg
        assert "falló" in msg
        assert "Resultado:" not in msg

    def test_killed_status(self) -> None:
        row = {
            "id": "a" * 32,
            "role": "ops",
            "goal": "deploy",
            "status": "killed",
            "result_summary": "",
        }
        msg = _format_notification(row)
        assert "⛔" in msg
        assert "detenido" in msg

    def test_timeout_status(self) -> None:
        row = {
            "id": "b" * 32,
            "role": "research",
            "goal": "analizar logs",
            "status": "timeout",
            "result_summary": "no terminó a tiempo",
        }
        msg = _format_notification(row)
        assert "⏰" in msg
        assert "tiempo" in msg

    def test_long_goal_is_truncated(self) -> None:
        row = {
            "id": "c" * 32,
            "role": "dev",
            "goal": "x" * 200,
            "status": "completed",
            "result_summary": None,
        }
        msg = _format_notification(row)
        assert "…" in msg

    def test_long_result_is_truncated(self) -> None:
        row = {
            "id": "d" * 32,
            "role": "dev",
            "goal": "tarea corta",
            "status": "completed",
            "result_summary": "r" * 400,
        }
        msg = _format_notification(row)
        # result should be capped at 300 + ellipsis
        result_line = [line for line in msg.splitlines() if line.startswith("Resultado:")]
        assert len(result_line) == 1
        assert "…" in result_line[0]


# ─── AgentNotifier ────────────────────────────────────────────────────────────


class FakeTg:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, html: str, **kwargs: object) -> int:
        self.sent.append((chat_id, html))
        return 1


class TestAgentNotifier:
    @pytest.mark.asyncio
    async def test_notifies_completed_agent(self) -> None:
        tg = FakeTg()
        notifier = AgentNotifier("postgresql://...", tg, chat_ids=[9999])

        real_row = {
            "id": "abc" * 10 + "def",
            "role": "dev",
            "goal": "do the thing",
            "status": "completed",
            "result_summary": "done",
            "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        }

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[real_row])
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            await notifier._check()

        assert len(tg.sent) == 1
        chat_id, html = tg.sent[0]
        assert chat_id == 9999
        assert "completó" in html

    @pytest.mark.asyncio
    async def test_no_notification_when_no_rows(self) -> None:
        tg = FakeTg()
        notifier = AgentNotifier("postgresql://...", tg, chat_ids=[9999])

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            await notifier._check()

        assert tg.sent == []

    @pytest.mark.asyncio
    async def test_db_connect_failure_is_swallowed(self) -> None:
        tg = FakeTg()
        notifier = AgentNotifier("postgresql://...", tg, chat_ids=[9999])

        with patch("asyncpg.connect", AsyncMock(side_effect=Exception("connection refused"))):
            await notifier._check()  # should not raise

        assert tg.sent == []

    @pytest.mark.asyncio
    async def test_watermark_advances_after_notification(self) -> None:
        tg = FakeTg()
        notifier = AgentNotifier("postgresql://...", tg, chat_ids=[9999])
        initial = notifier._last_seen

        # Use a timestamp in the future to ensure it's > initial (which is datetime.now())
        from datetime import timedelta

        newer_ts = initial + timedelta(seconds=60)
        real_row = {
            "id": "x" * 32,
            "role": "dev",
            "goal": "task",
            "status": "completed",
            "result_summary": None,
            "updated_at": newer_ts,
        }

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[real_row])
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            await notifier._check()

        assert notifier._last_seen == newer_ts
        assert notifier._last_seen > initial

    @pytest.mark.asyncio
    async def test_run_cancellable(self) -> None:
        """run() should stop cleanly when cancelled."""
        tg = FakeTg()
        notifier = AgentNotifier("postgresql://...", tg, chat_ids=[9999], poll_interval=100)

        task = asyncio.create_task(notifier.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ─── Bot /agents and /instruct ───────────────────────────────────────────────


class TestBotAgentsCommand:
    @pytest.mark.asyncio
    async def test_agents_no_db_url(self) -> None:
        """When lina_db_url is None, send a warning message."""
        from lina_gateway.bot import Bot
        from lina_gateway.config import Config

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123:T",
                "GOOSED_URL": "http://goosed:3000",
                "GOOSE_SERVER_SECRET_KEY": "s",
            },
            clear=False,
        ):
            cfg = Config()

        cfg.lina_db_url = None
        bot = Bot(cfg)

        sent = []

        async def fake_send(chat_id, html, **kwargs):
            sent.append(html)
            return 1

        bot._tg.send_message = fake_send
        await bot._handle_agents(42)

        assert len(sent) == 1
        assert "no disponible" in sent[0]

    @pytest.mark.asyncio
    async def test_agents_empty_result(self) -> None:
        """When no active agents, show 'no hay agentes'."""
        from lina_gateway.bot import Bot
        from lina_gateway.config import Config

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123:T",
                "GOOSED_URL": "http://goosed:3000",
                "GOOSE_SERVER_SECRET_KEY": "s",
                "LINA_DB_URL": "postgresql://lina:pw@localhost/lina",
            },
            clear=False,
        ):
            cfg = Config()

        bot = Bot(cfg)

        sent = []

        async def fake_send(chat_id, html, **kwargs):
            sent.append(html)
            return 1

        bot._tg.send_message = fake_send

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
            await bot._handle_agents(42)

        assert "activos" in sent[0] or "No hay" in sent[0]

    @pytest.mark.asyncio
    async def test_agents_db_error_is_reported(self) -> None:
        """DB errors are caught and sent as a user-facing message."""
        from lina_gateway.bot import Bot
        from lina_gateway.config import Config

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123:T",
                "GOOSED_URL": "http://goosed:3000",
                "GOOSE_SERVER_SECRET_KEY": "s",
                "LINA_DB_URL": "postgresql://bad/lina",
            },
            clear=False,
        ):
            cfg = Config()

        bot = Bot(cfg)

        sent = []

        async def fake_send(chat_id, html, **kwargs):
            sent.append(html)
            return 1

        bot._tg.send_message = fake_send

        with patch("asyncpg.connect", AsyncMock(side_effect=Exception("refused"))):
            await bot._handle_agents(42)

        assert len(sent) == 1
        assert "Error" in sent[0] or "error" in sent[0]


class TestBotInstructExpansion:
    """Test that /instruct rewrites text before passing to goosed."""

    @pytest.mark.asyncio
    async def test_instruct_missing_args_returns_usage(self) -> None:
        from unittest.mock import patch

        from lina_gateway.bot import Bot
        from lina_gateway.config import Config
        from lina_gateway.telegram_client import TelegramMessage

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123:T",
                "GOOSED_URL": "http://goosed:3000",
                "GOOSE_SERVER_SECRET_KEY": "s",
            },
            clear=False,
        ):
            cfg = Config()

        bot = Bot(cfg)
        sent = []

        async def fake_send(chat_id, html, **kwargs):
            sent.append(html)
            return 1

        bot._tg.send_message = fake_send

        msg = MagicMock(spec=TelegramMessage)
        msg.chat = MagicMock()
        msg.chat.id = 42
        msg.text = "/instruct"
        msg.voice = None
        msg.message_id = 1

        await bot._handle(msg)

        assert len(sent) == 1
        assert "Uso:" in sent[0]

    @pytest.mark.asyncio
    async def test_instruct_with_only_id_no_text_returns_usage(self) -> None:

        from lina_gateway.bot import Bot
        from lina_gateway.config import Config
        from lina_gateway.telegram_client import TelegramMessage

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123:T",
                "GOOSED_URL": "http://goosed:3000",
                "GOOSE_SERVER_SECRET_KEY": "s",
            },
            clear=False,
        ):
            cfg = Config()

        bot = Bot(cfg)
        sent = []

        async def fake_send(chat_id, html, **kwargs):
            sent.append(html)
            return 1

        bot._tg.send_message = fake_send

        msg = MagicMock(spec=TelegramMessage)
        msg.chat = MagicMock()
        msg.chat.id = 42
        msg.text = "/instruct abc123"
        msg.voice = None
        msg.message_id = 1

        await bot._handle(msg)

        assert len(sent) == 1
        assert "Uso:" in sent[0]

"""Unit tests for lina_gateway.boot_hook (issue #49)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from lina_gateway.boot_hook import (
    _INTERRUPTION_WINDOW,
    get_last_messages,
    on_boot,
    on_shutdown,
    save_message,
)

# ─── Test doubles ─────────────────────────────────────────────────────────────


class FakeTg:
    """Minimal Telegram sender that records (chat_id, text) calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str) -> int | None:
        if self._fail:
            raise OSError("network error")
        self.sent.append((chat_id, text))
        return len(self.sent)  # fake message_id


# ─── on_boot ─────────────────────────────────────────────────────────────────


class TestOnBootNoDB:
    """on_boot when no DB URL is provided."""

    @pytest.mark.asyncio
    async def test_no_chat_ids_sends_nothing(self) -> None:
        tg = FakeTg()
        await on_boot(tg, db_url=None, chat_ids=[])
        assert tg.sent == []

    @pytest.mark.asyncio
    async def test_sends_online_message(self) -> None:
        tg = FakeTg()
        await on_boot(tg, db_url=None, chat_ids=[1001])
        assert len(tg.sent) == 1
        chat_id, text = tg.sent[0]
        assert chat_id == 1001
        assert "LINA online" in text

    @pytest.mark.asyncio
    async def test_multiple_chat_ids(self) -> None:
        tg = FakeTg()
        await on_boot(tg, db_url=None, chat_ids=[1001, 1002, 1003])
        assert len(tg.sent) == 3
        assert {c for c, _ in tg.sent} == {1001, 1002, 1003}

    @pytest.mark.asyncio
    async def test_tg_failure_does_not_raise(self) -> None:
        """Telegram errors are swallowed — gateway must continue."""
        tg = FakeTg(fail=True)
        await on_boot(tg, db_url=None, chat_ids=[1001])  # must not raise


class TestOnBootWithDB:
    """on_boot when a DB URL is provided (DB helpers are mocked)."""

    @pytest.mark.asyncio
    async def test_graceful_previous_shutdown_sends_online(self) -> None:
        tg = FakeTg()
        now = datetime.now(UTC)
        with (
            patch(
                "lina_gateway.boot_hook._last_event",
                new=AsyncMock(return_value=("shutdown", now)),
            ),
            patch("lina_gateway.boot_hook._record_event", new=AsyncMock()),
        ):
            await on_boot(tg, db_url="postgresql://fake", chat_ids=[1001])

        assert len(tg.sent) == 1
        assert "LINA online" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_recent_started_event_sends_crash_message(self) -> None:
        """Last event was 'started' and is recent → crash restart."""
        tg = FakeTg()
        recent = datetime.now(UTC) - timedelta(minutes=2)  # within 10-min window
        with (
            patch(
                "lina_gateway.boot_hook._last_event",
                new=AsyncMock(return_value=("started", recent)),
            ),
            patch("lina_gateway.boot_hook._record_event", new=AsyncMock()),
        ):
            await on_boot(tg, db_url="postgresql://fake", chat_ids=[1001])

        assert len(tg.sent) == 1
        text = tg.sent[0][1]
        assert "reiniciada inesperadamente" in text.lower() or "reinici" in text.lower()

    @pytest.mark.asyncio
    async def test_old_started_event_sends_online_message(self) -> None:
        """Last event was 'started' but is older than the window → clean start."""
        tg = FakeTg()
        old = datetime.now(UTC) - _INTERRUPTION_WINDOW - timedelta(minutes=5)
        with (
            patch(
                "lina_gateway.boot_hook._last_event",
                new=AsyncMock(return_value=("started", old)),
            ),
            patch("lina_gateway.boot_hook._record_event", new=AsyncMock()),
        ):
            await on_boot(tg, db_url="postgresql://fake", chat_ids=[1001])

        assert "LINA online" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_no_previous_event_sends_online(self) -> None:
        """Empty table (first boot ever) → online message."""
        tg = FakeTg()
        with (
            patch(
                "lina_gateway.boot_hook._last_event",
                new=AsyncMock(return_value=(None, None)),
            ),
            patch("lina_gateway.boot_hook._record_event", new=AsyncMock()),
        ):
            await on_boot(tg, db_url="postgresql://fake", chat_ids=[1001])

        assert "LINA online" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_db_failure_still_sends_notification(self) -> None:
        """_last_event and _record_event swallow all errors internally.
        Even when both return None/nothing (simulating DB down), the hook
        must still send the online notification."""
        tg = FakeTg()
        with (
            patch(
                "lina_gateway.boot_hook._last_event",
                # Real _last_event returns (None, None) on any DB error.
                new=AsyncMock(return_value=(None, None)),
            ),
            patch(
                "lina_gateway.boot_hook._record_event",
                new=AsyncMock(),  # No-op, simulating swallowed write error.
            ),
        ):
            await on_boot(tg, db_url="postgresql://fake", chat_ids=[1001])

        # Even with DB down, a notification should be sent.
        assert len(tg.sent) == 1


# ─── on_shutdown ─────────────────────────────────────────────────────────────


class TestOnShutdown:
    @pytest.mark.asyncio
    async def test_no_chat_ids_sends_nothing(self) -> None:
        tg = FakeTg()
        with patch("lina_gateway.boot_hook._record_event", new=AsyncMock()):
            await on_shutdown(tg, db_url=None, chat_ids=[])
        assert tg.sent == []

    @pytest.mark.asyncio
    async def test_sends_shutdown_message(self) -> None:
        tg = FakeTg()
        await on_shutdown(tg, db_url=None, chat_ids=[1001])
        assert len(tg.sent) == 1
        assert "Reiniciándome" in tg.sent[0][1]

    @pytest.mark.asyncio
    async def test_multiple_chat_ids(self) -> None:
        tg = FakeTg()
        await on_shutdown(tg, db_url=None, chat_ids=[1001, 1002])
        assert len(tg.sent) == 2

    @pytest.mark.asyncio
    async def test_tg_failure_does_not_raise(self) -> None:
        tg = FakeTg(fail=True)
        await on_shutdown(tg, db_url=None, chat_ids=[1001])  # must not raise

    @pytest.mark.asyncio
    async def test_records_db_event_when_db_url_set(self) -> None:
        tg = FakeTg()
        recorded: list[tuple[str, str]] = []

        async def fake_record(db_url: str, event_type: str) -> None:
            recorded.append((db_url, event_type))

        with patch("lina_gateway.boot_hook._record_event", new=fake_record):
            await on_shutdown(tg, db_url="postgresql://fake", chat_ids=[1001])

        assert recorded == [("postgresql://fake", "shutdown")]

    @pytest.mark.asyncio
    async def test_no_db_record_when_db_url_none(self) -> None:
        tg = FakeTg()
        called = []

        async def fake_record(db_url: str, event_type: str) -> None:  # noqa: ARG001
            called.append(event_type)

        with patch("lina_gateway.boot_hook._record_event", new=fake_record):
            await on_shutdown(tg, db_url=None, chat_ids=[1001])

        assert called == []


# ─── save_message / get_last_messages (issue #56) ────────────────────────────


class TestSaveMessage:
    """save_message swallows errors — never raises to caller."""

    @pytest.mark.asyncio
    async def test_swallows_asyncpg_import_error(self) -> None:
        """If asyncpg is unavailable, save_message must not raise."""
        import sys

        original = sys.modules.get("asyncpg")
        sys.modules["asyncpg"] = None  # type: ignore[assignment]
        try:
            await save_message("postgresql://fake", "telegram-1", "user", "hello")
        finally:
            if original is None:
                del sys.modules["asyncpg"]
            else:
                sys.modules["asyncpg"] = original

    @pytest.mark.asyncio
    async def test_swallows_connection_error(self) -> None:
        """If the DB is unreachable, save_message must not raise."""
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            # Should not raise
            await save_message("postgresql://fake", "telegram-1", "user", "hello")


class TestGetLastMessages:
    """get_last_messages returns [] on any failure, never raises."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self) -> None:
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            result = await get_last_messages("postgresql://fake", "telegram-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_messages_in_order(self) -> None:
        """Returned messages are oldest-first (turn_number ASC)."""
        import asyncpg

        rows = [
            {"role": "user", "content": "hola", "turn_number": 0},
            {"role": "assistant", "content": "buenas", "turn_number": 1},
        ]

        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=rows)
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            result = await get_last_messages("postgresql://fake", "telegram-1", limit=5)

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hola"}
        assert result[1] == {"role": "assistant", "content": "buenas"}

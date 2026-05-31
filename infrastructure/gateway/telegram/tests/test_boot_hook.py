"""Unit tests for lina_gateway.boot_hook (issue #49)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from lina_gateway.boot_hook import (
    _INTERRUPTION_WINDOW,
    SmartContext,
    _calculate_cost,
    _estimate_tokens,
    get_last_messages,
    get_smart_context,
    on_boot,
    on_shutdown,
    save_message,
    save_token_usage,
    save_trace,
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


# ─── SmartContext ─────────────────────────────────────────────────────────────


class TestSmartContext:
    """SmartContext.has_data and format_warmup_prompt logic."""

    def test_empty_has_no_data(self) -> None:
        ctx = SmartContext(summary="", messages=[])
        assert ctx.has_data is False

    def test_summary_only_has_data(self) -> None:
        ctx = SmartContext(summary="Hicimos deploy del gateway.", messages=[])
        assert ctx.has_data is True

    def test_messages_only_has_data(self) -> None:
        ctx = SmartContext(summary="", messages=[{"role": "user", "content": "hola"}])
        assert ctx.has_data is True

    def test_format_warmup_includes_summary(self) -> None:
        ctx = SmartContext(summary="El deploy salió bien.", messages=[])
        prompt = ctx.format_warmup_prompt()
        assert "El deploy salió bien." in prompt
        assert "CONTEXTO_RECUPERADO_AUTOMATICAMENTE" in prompt
        assert "✅ Sesión reanudada." in prompt

    def test_format_warmup_includes_messages(self) -> None:
        ctx = SmartContext(
            summary="",
            messages=[
                {"role": "user", "content": "hola LINA"},
                {"role": "assistant", "content": "buenas Fede"},
            ],
        )
        prompt = ctx.format_warmup_prompt()
        assert "Fede: hola LINA" in prompt
        assert "LINA: buenas Fede" in prompt

    def test_format_warmup_both_sections(self) -> None:
        ctx = SmartContext(
            summary="Resumen de sesión.",
            messages=[{"role": "user", "content": "ok"}],
        )
        prompt = ctx.format_warmup_prompt()
        assert "## Resumen de sesión anterior" in prompt
        assert "## Últimos mensajes" in prompt


# ─── get_smart_context ───────────────────────────────────────────────────────


class TestGetSmartContext:
    """get_smart_context returns SmartContext, never raises."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self) -> None:
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            ctx = await get_smart_context("postgresql://fake", "telegram-1")

        assert not ctx.has_data
        assert ctx.summary == ""
        assert ctx.messages == []

    @pytest.mark.asyncio
    async def test_combines_summary_and_messages(self) -> None:
        """When DB has a summary and recent messages, both are returned."""
        import asyncpg

        summary_row = {"raw_summary": "Deploy del gateway completado."}
        message_rows = [
            {"role": "user", "content": "listo?", "turn_number": 5},
            {"role": "assistant", "content": "sí, online", "turn_number": 6},
        ]

        fake_conn = AsyncMock()
        fake_conn.fetchrow = AsyncMock(return_value=summary_row)
        fake_conn.fetch = AsyncMock(return_value=message_rows)
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            ctx = await get_smart_context("postgresql://fake", "telegram-1")

        assert ctx.has_data is True
        assert ctx.summary == "Deploy del gateway completado."
        assert len(ctx.messages) == 2

    @pytest.mark.asyncio
    async def test_no_summary_but_has_messages(self) -> None:
        """When there's no summary yet, messages alone are returned."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetchrow = AsyncMock(return_value=None)  # no summary row
        fake_conn.fetch = AsyncMock(
            return_value=[{"role": "user", "content": "hola", "turn_number": 0}]
        )
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            ctx = await get_smart_context("postgresql://fake", "telegram-1")

        assert ctx.has_data is True
        assert ctx.summary == ""
        assert len(ctx.messages) == 1

    @pytest.mark.asyncio
    async def test_empty_db_returns_no_data(self) -> None:
        """First-ever session: no summary, no messages — has_data is False."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetchrow = AsyncMock(return_value=None)
        fake_conn.fetch = AsyncMock(return_value=[])
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            ctx = await get_smart_context("postgresql://fake", "telegram-1")

        assert ctx.has_data is False


# ─── Token / cost metering (issue #61) ────────────────────────────────────────


class TestEstimateTokens:
    """_estimate_tokens: character-based estimation."""

    def test_empty_string_returns_zero(self) -> None:
        assert _estimate_tokens("") == 0

    def test_three_chars_is_one_token(self) -> None:
        assert _estimate_tokens("abc") == 1

    def test_ceiling_division(self) -> None:
        # 4 chars → ceil(4/3) = 2
        assert _estimate_tokens("abcd") == 2

    def test_long_text(self) -> None:
        text = "hello world " * 100  # 1200 chars → 400 tokens
        assert _estimate_tokens(text) == 400

    def test_returns_at_least_one_for_nonempty(self) -> None:
        assert _estimate_tokens("x") == 1


class TestCalculateCost:
    """_calculate_cost: price dict lookup and arithmetic."""

    def test_flash_model(self) -> None:
        # 1M input @ $0.14 + 1M output @ $0.28 = $0.42
        cost = _calculate_cost("deepseek-v4-flash", 1_000_000, 1_000_000)
        assert abs(cost - 0.42) < 1e-6

    def test_pro_model(self) -> None:
        # 1M input (cache miss) @ $0.435 + 1M output @ $0.87 = $1.305
        cost = _calculate_cost("deepseek-v4-pro", 1_000_000, 1_000_000)
        assert abs(cost - 1.305) < 1e-6

    def test_legacy_alias_chat(self) -> None:
        cost_chat = _calculate_cost("deepseek-chat", 100_000, 100_000)
        cost_flash = _calculate_cost("deepseek-v4-flash", 100_000, 100_000)
        assert cost_chat == cost_flash

    def test_unknown_model_falls_back_to_flash(self) -> None:
        cost_unknown = _calculate_cost("unknown-model-xyz", 100_000, 100_000)
        cost_flash = _calculate_cost("deepseek-v4-flash", 100_000, 100_000)
        assert cost_unknown == cost_flash

    def test_zero_tokens_is_zero_cost(self) -> None:
        assert _calculate_cost("deepseek-v4-flash", 0, 0) == 0.0


class TestSaveTokenUsage:
    """save_token_usage: DB write + error handling."""

    @pytest.mark.asyncio
    async def test_swallows_connection_error(self) -> None:
        """DB unreachable → no exception raised."""
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            # Must not raise
            await save_token_usage(
                "postgresql://fake",
                "telegram-1",
                input_tokens=100,
                output_tokens=50,
                accumulated_cost_usd=0.00025,
            )

    @pytest.mark.asyncio
    async def test_inserts_row_with_real_tokens(self) -> None:
        """Real token values from goosed token_state are persisted correctly."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetchval = AsyncMock(return_value=None)  # no prior row → delta = full cost
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await save_token_usage(
                "postgresql://fake",
                "telegram-99",
                input_tokens=420,
                output_tokens=85,
                accumulated_cost_usd=0.00025,
            )

        assert fake_conn.execute.called
        call_args = fake_conn.execute.call_args[0]
        # positional args after the SQL: session_id, model, prompt_chars,
        # completion_chars, prompt_tokens_est, completion_tokens_est,
        # cost_usd_est, accumulated_cost_usd
        params = call_args[1:]
        assert params[0] == "telegram-99"  # session_id
        assert params[1] == "deepseek-v4-flash"  # model
        assert params[2] == 0  # prompt_chars (not tracked with real tokens)
        assert params[3] == 0  # completion_chars
        assert params[4] == 420  # prompt_tokens_est (real)
        assert params[5] == 85  # completion_tokens_est (real)
        assert params[6] == 0.00025  # cost_usd_est = delta (no prior row)
        assert params[7] == 0.00025  # accumulated_cost_usd

    @pytest.mark.asyncio
    async def test_per_turn_cost_delta(self) -> None:
        """Turn cost = accumulatedCost - last recorded accumulated cost."""
        import asyncpg

        fake_conn = AsyncMock()
        # Simulate prior row with accumulated_cost_usd = 0.00100
        fake_conn.fetchval = AsyncMock(return_value=0.00100)
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await save_token_usage(
                "postgresql://fake",
                "telegram-99",
                input_tokens=200,
                output_tokens=60,
                accumulated_cost_usd=0.00152,
            )

        call_args = fake_conn.execute.call_args[0]
        params = call_args[1:]
        assert round(params[6], 8) == round(0.00052, 8)  # cost_usd_est = delta
        assert params[7] == 0.00152  # accumulated_cost_usd stored as-is


# ─── save_trace ───────────────────────────────────────────────────────────────


class TestSaveTrace:
    """save_trace: reasoning trace persistence (issue #62)."""

    @pytest.mark.asyncio
    async def test_swallows_connection_error(self) -> None:
        """DB unreachable → no exception raised."""
        import asyncpg

        with patch.object(asyncpg, "connect", new=AsyncMock(side_effect=OSError("no db"))):
            await save_trace("postgresql://fake", "telegram-1", "razoné algo")

    @pytest.mark.asyncio
    async def test_empty_thinking_skipped(self) -> None:
        """Empty/whitespace thinking_text must not hit the DB."""
        import asyncpg

        fake_connect = AsyncMock()
        with patch.object(asyncpg, "connect", new=fake_connect):
            await save_trace("postgresql://fake", "telegram-1", "   ")
        fake_connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_row_with_thinking_text(self) -> None:
        """Thinking text and prompt_hash are persisted correctly."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetchval = AsyncMock(return_value=3)  # turn_number = 3
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await save_trace(
                "postgresql://fake",
                "telegram-42",
                "Primero necesito obtener el attempt_id",
                "abcdef1234",
            )

        assert fake_conn.execute.called
        call_args = fake_conn.execute.call_args[0]
        params = call_args[1:]
        assert params[0] == "telegram-42"  # session_id
        assert params[1] == 3  # turn_number from fetchval
        assert params[2] == "Primero necesito obtener el attempt_id"  # thinking_text
        assert params[3] == "abcdef1234"  # prompt_hash
        assert params[4] == "deepseek-v4-flash"  # default model

    @pytest.mark.asyncio
    async def test_default_turn_number_zero_on_new_session(self) -> None:
        """When no prior trace exists, turn_number defaults to 0."""
        import asyncpg

        fake_conn = AsyncMock()
        fake_conn.fetchval = AsyncMock(return_value=0)
        fake_conn.execute = AsyncMock()
        fake_conn.close = AsyncMock()

        with patch.object(asyncpg, "connect", new=AsyncMock(return_value=fake_conn)):
            await save_trace("postgresql://fake", "telegram-new", "pensé algo nuevo")

        params = fake_conn.execute.call_args[0][1:]
        assert params[1] == 0  # turn_number = 0

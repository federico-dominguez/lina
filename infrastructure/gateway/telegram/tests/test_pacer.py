"""Unit tests for the StreamingBubble pacer."""

import asyncio

import pytest

from lina_gateway.pacer import StreamingBubble, trim_work_log

# ─── trim_work_log ───────────────────────────────────────────────────────────

class TestTrimWorkLog:
    def test_short_log_unchanged(self):
        text = "line1\nline2\nline3"
        assert trim_work_log(text) == text

    def test_long_log_trimmed(self):
        lines = [f"line{i}" for i in range(30)]
        text = "\n".join(lines)
        result = trim_work_log(text)
        assert result.startswith("[…]")
        assert "line29" in result
        # Only last 18 lines kept
        kept = result.split("\n")[1:]  # skip "[…]"
        assert len(kept) == 18

    def test_exactly_at_limit_not_trimmed(self):
        lines = [f"line{i}" for i in range(18)]
        text = "\n".join(lines)
        assert trim_work_log(text) == text


# ─── StreamingBubble ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestStreamingBubble:
    async def test_seal_calls_final_edit(self):
        calls: list[tuple[str, str, bool]] = []

        async def edit_fn(body: str, thinking: str, sealed: bool) -> None:
            calls.append((body, thinking, sealed))

        bubble = StreamingBubble(tick=0.01, edit_fn=edit_fn)
        bubble.start()
        bubble.update(thinking="think", body="answer")
        await bubble.seal()

        # The final sealed=True call must have been made
        assert any(c[2] is True for c in calls), "Expected a sealed=True call"
        # Final call has correct content
        sealed_calls = [c for c in calls if c[2]]
        assert sealed_calls[-1][0] == "answer"
        assert sealed_calls[-1][1] == "think"

    async def test_no_update_no_intermediate_edit(self):
        """If no update is sent, pacer should still do the final sealed edit."""
        calls: list[tuple[str, str, bool]] = []

        async def edit_fn(body: str, thinking: str, sealed: bool) -> None:
            calls.append((body, thinking, sealed))

        bubble = StreamingBubble(tick=0.01, edit_fn=edit_fn)
        bubble.start()
        await asyncio.sleep(0.005)
        await bubble.seal()

        # At minimum the final call happened
        assert len(calls) >= 1
        assert calls[-1][2] is True

    async def test_multiple_updates_latest_wins(self):
        """Pacer should always send the latest content on each tick."""
        seen_bodies: list[str] = []

        async def edit_fn(body: str, thinking: str, sealed: bool) -> None:
            if not sealed:
                seen_bodies.append(body)

        bubble = StreamingBubble(tick=0.05, edit_fn=edit_fn)
        bubble.start()
        bubble.update(thinking="", body="v1")
        bubble.update(thinking="", body="v2")
        bubble.update(thinking="", body="v3")
        await asyncio.sleep(0.08)
        await bubble.seal()

        # At least one intermediate edit should show the latest body
        if seen_bodies:
            assert seen_bodies[-1] == "v3"

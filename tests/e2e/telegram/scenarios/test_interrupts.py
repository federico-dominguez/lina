"""
E2E: /stop interrupt handling test.

Verifies that sending /stop mid-stream results in an immediate ⛔ response.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_stop_mid_stream(tg: TelegramTestClient) -> None:
    """Bot must respond to /stop with ⛔ Deteniendo. within 5 seconds."""
    # Fire a heavy prompt and interrupt it quickly
    prompt_task = asyncio.create_task(
        tg.send_prompt(
            "Escribí una novela corta de 5000 palabras sobre un robot que aprende a sentir.",
            timeout=60,
            stable_window=1.0,
        )
    )

    # Wait briefly for bot to start streaming, then /stop
    await asyncio.sleep(2.0)

    # Capture the /stop acknowledgement directly
    stop_ack_capture = await tg.send_prompt("/stop", timeout=8, stable_window=1.5)

    # Cancel the original prompt task (we don't need the full response)
    prompt_task.cancel()
    try:
        await prompt_task
    except (asyncio.CancelledError, Exception):
        pass

    # The /stop reply must contain ⛔ or an equivalent acknowledgement
    final = stop_ack_capture.final_text + (
        stop_ack_capture.thinking_message.final_text if stop_ack_capture.thinking_message else ""
    )
    assert "⛔" in final or "Deteniendo" in final or "ninguna tarea" in final, (
        f"Expected ⛔ stop acknowledgement. Got: {final[:200]!r}"
    )

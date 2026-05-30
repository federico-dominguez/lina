"""
E2E: Streaming latency and typewriter cadence tests.

Measures TTFT, TTLT and edit interval distribution.
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_not_empty,
    assert_ttft_under,
    assert_ttlt_under,
    assert_typewriter_cadence,
)
from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_ttft_short_prompt(tg: TelegramTestClient) -> None:
    """TTFT must be < 5s for a trivial prompt."""
    capture = await tg.send_prompt("¿Cuánto es 2 + 2?")
    assert_not_empty(capture)
    assert_ttft_under(capture, seconds=5.0)


@pytest.mark.e2e_telegram
async def test_ttlt_short_prompt(tg: TelegramTestClient) -> None:
    """TTLT must be < 30s for a short prompt."""
    capture = await tg.send_prompt("¿Cuál es la capital de Francia? Respondé en una sola línea.")
    assert_not_empty(capture)
    assert_ttlt_under(capture, seconds=30.0)


@pytest.mark.e2e_telegram
async def test_typewriter_cadence(tg: TelegramTestClient) -> None:
    """Edit intervals must be between 100ms and 3000ms (no spam, no stalls)."""
    capture = await tg.send_prompt(
        "Escribí un párrafo de unas 200 palabras sobre la historia de Linux."
    )
    assert_not_empty(capture)
    assert_typewriter_cadence(capture, min_interval_ms=100, max_interval_ms=3000)


@pytest.mark.e2e_telegram
async def test_metrics_logged(tg: TelegramTestClient, caplog: pytest.LogCaptureFixture) -> None:
    """Capture summary must produce ttft, ttlt, edits metrics."""
    import logging

    with caplog.at_level(logging.INFO, logger="tests.e2e.telegram.client"):
        capture = await tg.send_prompt("Decí 'hola'.")

    assert_not_empty(capture)
    assert capture.ttft is not None, "ttft should be set"
    assert capture.ttlt is not None, "ttlt should be set"
    summary = capture.summary()
    assert "ttft=" in summary
    assert "ttlt=" in summary
    assert "edits=" in summary

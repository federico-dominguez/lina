"""
E2E: Thinking block separation tests.

Verifies that the 💭 Razonando... block is a separate message from the
final response and that neither bleeds into the other.
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_full_integrity,
    assert_thinking_max_length,
    assert_thinking_not_in_final,
    assert_thinking_separated,
)
from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_thinking_is_separate_message(tg: TelegramTestClient) -> None:
    """Thinking block must arrive as a distinct message_id from the final reply."""
    # Use a reasoning-heavy prompt that forces DeepSeek to think
    capture = await tg.send_prompt(
        "Razoná paso a paso: si tengo 3 cajas, cada una con 4 pelotas, "
        "y saco 2 pelotas de cada caja, ¿cuántas pelotas quedan en total?"
    )
    assert_thinking_separated(capture)


@pytest.mark.e2e_telegram
async def test_thinking_not_leaked_in_final(tg: TelegramTestClient) -> None:
    """Final response must not contain 💭 or chain-of-thought fragments."""
    capture = await tg.send_prompt(
        "Explicame brevemente qué es un árbol binario de búsqueda."
    )
    assert_thinking_not_in_final(capture)


@pytest.mark.e2e_telegram
async def test_thinking_length_capped(tg: TelegramTestClient) -> None:
    """Thinking block must be ≤ 800 chars (AGENTS.md §3)."""
    capture = await tg.send_prompt(
        "Resolvé este problema matemático: ¿cuántos números primos hay menores que 100? "
        "Enumeralos."
    )
    assert_thinking_max_length(capture, max_chars=800)


@pytest.mark.e2e_telegram
async def test_no_thinking_on_simple_greeting(tg: TelegramTestClient) -> None:
    """A simple greeting should produce a quick response without a lengthy thinking block."""
    capture = await tg.send_prompt("Hola")
    assert_full_integrity(capture, context="greeting")
    # Thinking block is allowed but if present must be short
    assert_thinking_max_length(capture, max_chars=800)

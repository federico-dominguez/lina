"""
E2E: Long response splitting tests.

Verifies that responses > 4096 chars are split into multiple valid messages
without corrupting HTML blocks or cutting inside <code>/<pre>.
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_full_integrity,
    assert_message_length,
    assert_no_open_tags_at_split,
    assert_valid_html,
)
from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_long_code_block_split(tg: TelegramTestClient) -> None:
    """A response containing a very long code block must split cleanly."""
    capture = await tg.send_prompt(
        "Escribí un script Python completo y muy detallado que implemente "
        "un servidor HTTP básico desde cero usando solo la stdlib, con comentarios "
        "exhaustivos en cada línea explicando qué hace. Debe tener al menos 150 líneas."
    )
    assert_full_integrity(capture, context="long_code_block")
    chunks = [m.final_text for m in capture.final_messages]
    for i, chunk in enumerate(chunks):
        assert_message_length(chunk, context=f"chunk_{i}")
        assert_valid_html(chunk, context=f"chunk_{i}")
    if len(chunks) > 1:
        assert_no_open_tags_at_split(chunks)


@pytest.mark.e2e_telegram
async def test_multiple_split_messages_are_valid(tg: TelegramTestClient) -> None:
    """Each part of a split response must independently be valid Telegram HTML."""
    capture = await tg.send_prompt(
        "Enumerá todos los comandos de Git que conocés con una descripción de 3 oraciones cada uno. "
        "Usá formato de lista numerada."
    )
    assert_full_integrity(capture, context="git_commands_list")
    for msg in capture.final_messages:
        assert_valid_html(msg.final_text, f"msg_id={msg.message_id}")
        assert_message_length(msg.final_text, context=f"msg_id={msg.message_id}")

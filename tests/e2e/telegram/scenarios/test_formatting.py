"""
E2E: HTML format rendering tests.

Verifies that each canonical format type (code block, shell output,
bullet lists, links, bold/italic, long response splitting) renders
correctly as Telegram HTML — no raw markdown leaks, valid tag structure.
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_contains_tag,
    assert_full_integrity,
    assert_no_markdown_leak,
    assert_valid_html,
)
from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_code_block_python(tg: TelegramTestClient) -> None:
    """Bot must wrap Python code in <pre><code>."""
    capture = await tg.send_prompt(
        "Mostrame un bloque de código Python con un hello world. "
        "Solo el código, sin explicación extra."
    )
    assert_full_integrity(capture, context="code_block_python")
    assert_contains_tag(capture, "pre", "code block expected")
    assert_contains_tag(capture, "code", "code tag expected")
    assert_no_markdown_leak(capture.final_text, "no backtick fences")


@pytest.mark.e2e_telegram
async def test_shell_output(tg: TelegramTestClient) -> None:
    """Bot must use <code> or <pre> for shell commands and output."""
    capture = await tg.send_prompt(
        "Ejecutá el comando `echo 'hola mundo'` y mostrame el output en un bloque de código."
    )
    assert_full_integrity(capture, context="shell_output")
    # At least one of pre or code
    has_pre = "<pre" in capture.final_text
    has_code = "<code" in capture.final_text
    assert has_pre or has_code, (
        "Expected <pre> or <code> for shell output. "
        f"Got: {capture.final_text[:300]!r}"
    )


@pytest.mark.e2e_telegram
async def test_bullet_list(tg: TelegramTestClient) -> None:
    """Bot must use bullet characters (•) instead of <ul>/<li> tags."""
    capture = await tg.send_prompt("Hacé una lista de exactamente 5 distros de Linux populares.")
    assert_full_integrity(capture, context="bullet_list")
    final = capture.final_text
    # Must NOT use unsupported <ul> or <li>
    assert "<ul>" not in final, "Found unsupported <ul> tag"
    assert "<li>" not in final, "Found unsupported <li> tag"
    # Should contain bullet or numbered list
    has_bullet = "•" in final or "·" in final
    has_numbered = any(f"{i}." in final for i in range(1, 6))
    assert has_bullet or has_numbered, (
        f"Expected bullet list markers. Got: {final[:300]!r}"
    )


@pytest.mark.e2e_telegram
async def test_inline_bold_and_italic(tg: TelegramTestClient) -> None:
    """Bot must use <b> and <i> for emphasis — not Markdown asterisks."""
    capture = await tg.send_prompt(
        "Respondé en una sola oración usando texto en negrita y texto en cursiva."
    )
    assert_full_integrity(capture, context="bold_italic")
    final = capture.final_text
    has_bold = "<b>" in final or "<strong>" in final
    has_italic = "<i>" in final or "<em>" in final
    assert has_bold or has_italic, (
        f"Expected <b> or <i> tags for emphasis. Got: {final[:300]!r}"
    )
    assert_no_markdown_leak(final, "no markdown bold/italic")


@pytest.mark.e2e_telegram
async def test_hyperlink(tg: TelegramTestClient) -> None:
    """Bot must use <a href='...'> for links."""
    capture = await tg.send_prompt(
        "Dame un link a la documentación oficial de Python. Solo el link, sin texto adicional."
    )
    assert_full_integrity(capture, context="hyperlink")
    assert_contains_tag(capture, "a", "link tag expected")
    assert "href=" in capture.final_text, "Expected href attribute in <a> tag"


@pytest.mark.e2e_telegram
async def test_long_response_split(tg: TelegramTestClient) -> None:
    """Bot must split responses > 4096 chars without corrupting HTML."""
    capture = await tg.send_prompt(
        "Escribí una descripción detallada de los 10 algoritmos de ordenamiento más conocidos. "
        "Para cada uno: nombre, complejidad, descripción de cómo funciona, ventajas y desventajas. "
        "Sé muy detallado."
    )
    assert_full_integrity(capture, context="long_response")
    # If there are multiple messages, each must be valid and length-compliant
    for msg in capture.final_messages:
        assert_valid_html(msg.final_text, f"msg_id={msg.message_id}")
        assert len(msg.final_text) <= 4096, (
            f"Message {msg.message_id} exceeds 4096 chars: {len(msg.final_text)}"
        )


@pytest.mark.e2e_telegram
async def test_inline_code(tg: TelegramTestClient) -> None:
    """Bot must use <code> for inline code references."""
    capture = await tg.send_prompt(
        "¿Qué hace la función `os.path.join()` en Python? Respondé en 2 oraciones."
    )
    assert_full_integrity(capture, context="inline_code")
    assert_contains_tag(capture, "code", "inline code expected for function reference")

"""
E2E: Regresión issue #31 — markdown crudo no debe filtrarse en Telegram HTML mode.

Tests de caja negra que verifican los 4 bugs corregidos:
  1. Texto en negrita/cursiva llega como <b>/<i>, no como **texto**
  2. Tablas con celdas en negrita no filtran asteriscos dentro de <pre><code>
  3. El bloque de razonamiento (thinking) no filtra markdown crudo
  4. URLs con underscores no se corrompen al aplicar italic
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_no_markdown_leak,
    assert_valid_html,
    assert_thinking_max_length,
)
from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_bold_text_no_asterisks(tg: TelegramTestClient) -> None:
    """Bug 1 & 3: negrita debe aparecer como <b>texto</b>, no **texto**."""
    capture = await tg.send_prompt(
        "Respondé exactamente con esto (sin cambiar nada): "
        "La respuesta es **2+2 = 4** y también __importante__."
    )
    final = capture.final_text
    assert "**" not in final, f"Raw ** found in response: {final[:300]!r}"
    assert "__importante__" not in final, f"Raw __ found in response: {final[:300]!r}"
    assert_no_markdown_leak(final, context="bold_text_no_asterisks")


@pytest.mark.e2e_telegram
async def test_table_with_bold_cells_no_asterisks(tg: TelegramTestClient) -> None:
    """Bug 3 (causa raíz): tablas con celdas en negrita no deben filtrar asteriscos."""
    capture = await tg.send_prompt(
        "Hacé una tabla Markdown con 2 columnas (Operación y Complejidad) "
        "y 3 filas. En la columna Operación usá texto en **negrita** para cada celda. "
        "Solo la tabla, sin texto extra."
    )
    final = capture.final_text
    assert "**" not in final, (
        f"Raw ** markdown found in table output: {final[:400]!r}"
    )
    assert_no_markdown_leak(final, context="table_bold_cells")


@pytest.mark.e2e_telegram
async def test_inline_formatting_in_response(tg: TelegramTestClient) -> None:
    """Bug 1: respuesta con negrita, cursiva y código inline debe usar HTML tags."""
    capture = await tg.send_prompt(
        "Explicá en una oración qué es Python usando **negrita** para el nombre, "
        "*cursiva* para 'lenguaje' y `código` para print()."
    )
    final = capture.final_text
    assert "**" not in final, f"Raw ** in: {final[:300]!r}"
    assert_no_markdown_leak(final, context="inline_formatting")
    # Should have proper HTML formatting
    assert_valid_html(final, context="inline_formatting")


@pytest.mark.e2e_telegram
async def test_url_with_underscores_not_corrupted(tg: TelegramTestClient) -> None:
    """Bug 2: URLs con underscores no deben partirse en tokens italic.

    Telethon devuelve el texto visible del link (no el href — Telegram lo guarda
    en entidades MessageEntityTextUrl separadas). Verificamos que el link text
    sea correcto y que no haya markdown leak ni fragmentos de italic espurios.
    """
    capture = await tg.send_prompt(
        "Mostrá este link exactamente: [Ver más](https://example.com/foo_bar_baz)"
    )
    final = capture.final_text
    # Telegram stores href in entities; Telethon returns visible text ("Ver más")
    # Verify at minimum the link text arrived intact
    assert "Ver" in final, f"Link text missing from response: {final[:300]!r}"
    # No raw markdown should appear — no **bold**, # headings, etc.
    assert_no_markdown_leak(final, context="url_underscores")
    # The unit test test_italic_underscore_not_matching_url validates href integrity


@pytest.mark.e2e_telegram
async def test_thinking_block_no_raw_markdown(tg: TelegramTestClient) -> None:
    """Bug 4: el bloque de razonamiento no debe filtrar markdown crudo."""
    capture = await tg.send_prompt(
        "Calculá cuántos segundos hay en un año. Mostrá tu razonamiento paso a paso."
    )
    thinking = capture.thinking_message
    if thinking is None:
        pytest.skip("No thinking block captured — DeepSeek thinking may be disabled")

    thinking_text = thinking.final_text
    assert "**" not in thinking_text, (
        f"Raw ** markdown in thinking block: {thinking_text[:300]!r}"
    )
    assert_no_markdown_leak(thinking_text, context="thinking_block")
    assert_thinking_max_length(capture)


@pytest.mark.e2e_telegram
async def test_heading_converted_to_bold(tg: TelegramTestClient) -> None:
    """Headings en markdown deben convertirse a <b>, no aparecer como # Título."""
    capture = await tg.send_prompt(
        "Respondé con exactamente este formato:\n"
        "# Título Principal\n"
        "Un párrafo de texto.\n"
        "## Subtítulo\n"
        "Otro párrafo."
    )
    final = capture.final_text
    assert "# Título" not in final, f"Raw # heading leaked: {final[:300]!r}"
    assert "## Subtítulo" not in final, f"Raw ## heading leaked: {final[:300]!r}"
    assert_no_markdown_leak(final, context="heading_conversion")

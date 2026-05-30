"""
E2E: Thinking bubble — comprehensive lifecycle tests.

Cubre todos los aspectos del bloque de razonamiento según issue #30:
  - El thinking llega como mensaje SEPARADO del body (distinto message_id)
  - El thinking crece en tiempo real (múltiples edits antes de sellar)
  - El thinking se sella (colapsa) antes de que llegue la respuesta final
  - El message_id del thinking es menor que el del body (primero en el chat)
  - Cadencia del typewriter es fluida (sin congelamientos ni ráfagas)
  - Thinking se trunca a MAX_THINKING_CHARS cuando es muy largo
  - No hay fragmentos de razonamiento en la respuesta final

  Tareas simples (sin tools):
    - Preguntas de razonamiento matemático/conceptual

  Tareas complejas (con tool calls):
    - Listar cursos de Moodle
    - Listar eventos de Google Calendar
    - Combinación de ambas

  Escenarios de borde:
    - Tarea trivial (¿cuánto es 2+2?) — thinking mínimo o ausente
    - Tarea que fuerza razonamiento largo — truncado correcto
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.assertions import (
    assert_full_integrity,
    assert_message_length,
    assert_not_empty,
    assert_thinking_max_length,
    assert_thinking_message_id_before_body,
    assert_thinking_not_in_final,
    assert_thinking_preceded_body,
    assert_thinking_realtime_updated,
    assert_thinking_sealed_before_body,
    assert_thinking_separated,
)
from tests.e2e.telegram.client import TelegramTestClient


# ─── Tareas simples ───────────────────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_thinking_is_separate_message_simple(tg: TelegramTestClient) -> None:
    """
    Tarea simple: el thinking block debe llegar como mensaje con message_id
    distinto al mensaje final.  Éste era el síntoma exacto de bug #30.
    """
    capture = await tg.send_prompt(
        "Razoná paso a paso: si tengo 3 cajas con 7 pelotas cada una "
        "y regalo la mitad del total, ¿cuántas pelotas me quedan?"
    )
    assert_thinking_separated(capture)
    assert_thinking_message_id_before_body(capture, "simple_boxes")
    assert_thinking_not_in_final(capture)


@pytest.mark.e2e_telegram
async def test_thinking_precedes_body_in_time(tg: TelegramTestClient) -> None:
    """El timestamp del primer snapshot del thinking < timestamp del primer body."""
    capture = await tg.send_prompt(
        "¿Cuántos segundos hay en una semana? Calculá paso a paso."
    )
    assert_thinking_separated(capture)
    assert_thinking_preceded_body(capture, "seconds_in_week")
    assert_thinking_message_id_before_body(capture, "seconds_in_week")


@pytest.mark.e2e_telegram
async def test_thinking_grows_in_realtime(tg: TelegramTestClient) -> None:
    """
    El thinking bubble debe recibir al menos 1 edición live antes de sellar.
    Si edit_count == 0 significa que la burbuja nunca se actualizó en tiempo real.
    """
    capture = await tg.send_prompt(
        "Explicame paso a paso cómo funciona el algoritmo QuickSort. "
        "Describí la recursión y el caso base.",
        timeout=45,
    )
    assert_thinking_separated(capture)
    assert_thinking_realtime_updated(capture, min_edits=1, context="quicksort")


@pytest.mark.e2e_telegram
async def test_thinking_sealed_before_body_arrives(tg: TelegramTestClient) -> None:
    """
    La última edición del thinking debe ocurrir antes (o al mismo tiempo)
    que la llegada del mensaje de body.  Verifica que se sella correctamente.
    """
    capture = await tg.send_prompt(
        "¿Cuáles son las diferencias clave entre TCP y UDP? "
        "Pensá bien antes de responder.",
        timeout=45,
    )
    assert_thinking_separated(capture)
    assert_thinking_sealed_before_body(capture, tolerance_s=1.5, context="tcp_vs_udp")
    assert_thinking_preceded_body(capture, "tcp_vs_udp")


@pytest.mark.e2e_telegram
async def test_thinking_typewriter_no_freeze(tg: TelegramTestClient) -> None:
    """
    Los intervalos entre edits del thinking bubble no deben superar 8 segundos
    (el pacer_tick es 1.5s; 8s es margen generoso para overhead de red).
    """
    capture = await tg.send_prompt(
        "Dame 5 razones concretas por las que es importante aprender algoritmos.",
        timeout=45,
    )
    thinking = capture.thinking_message
    if thinking and thinking.edit_count >= 2:
        for interval_ms in thinking.edit_intervals_ms:
            assert interval_ms <= 8_000, (
                f"Thinking typewriter se congeló por {interval_ms:.0f}ms "
                f"(máximo esperado 8000ms). El pacer puede estar roto."
            )


@pytest.mark.e2e_telegram
async def test_thinking_not_in_final_body(tg: TelegramTestClient) -> None:
    """La respuesta final no debe contener fragmentos del chain-of-thought."""
    capture = await tg.send_prompt(
        "¿Qué es la notación O(n log n)? Dame una explicación concisa."
    )
    assert_thinking_not_in_final(capture)
    assert_not_empty(capture)


@pytest.mark.e2e_telegram
async def test_thinking_max_chars_respected(tg: TelegramTestClient) -> None:
    """
    El thinking block debe estar truncado a MAX_THINKING_CHARS (800 chars).
    Forzamos un razonamiento largo pidiendo que liste todos los números primos < 100.
    """
    capture = await tg.send_prompt(
        "Enumerá todos los números primos menores que 100 y explicá por qué "
        "cada uno es primo. Justificá con detalle.",
        timeout=60,
    )
    assert_thinking_max_length(capture, max_chars=800)
    if capture.thinking_message:
        assert_thinking_separated(capture)


@pytest.mark.e2e_telegram
async def test_thinking_full_lifecycle_simple(tg: TelegramTestClient) -> None:
    """
    Ciclo de vida completo del thinking bubble en una tarea simple:
    1. Thinking llega como mensaje propio (msg_id distinto)
    2. Thinking tiene message_id < body message_id
    3. Thinking timestamp < body timestamp
    4. Thinking se sella antes de que llegue el body
    5. Sin thinking en la respuesta final
    6. Thinking ≤ 800 chars
    7. Respuesta final tiene HTML válido, sin markdown leak
    """
    capture = await tg.send_prompt(
        "Explicame cómo funciona el algoritmo de Dijkstra. "
        "Describí los pasos y la condición de terminación.",
        timeout=45,
    )
    assert_thinking_separated(capture)
    assert_thinking_message_id_before_body(capture, "dijkstra")
    assert_thinking_preceded_body(capture, "dijkstra")
    assert_thinking_sealed_before_body(capture, tolerance_s=1.5, context="dijkstra")
    assert_thinking_not_in_final(capture)
    assert_thinking_max_length(capture, max_chars=800)
    assert_full_integrity(capture, context="dijkstra")


# ─── Tarea trivial ───────────────────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_trivial_query_minimal_thinking(tg: TelegramTestClient) -> None:
    """
    Para consultas triviales (ej: 2+2), el thinking puede ser muy breve o ausente.
    Si existe, debe estar separado y ser válido.
    """
    capture = await tg.send_prompt("¿Cuánto es 2+2?")
    assert_not_empty(capture)
    if capture.thinking_message:
        assert_thinking_separated(capture)
        assert_thinking_max_length(capture, max_chars=800)
        assert_thinking_not_in_final(capture)
    # La respuesta debe contener "4"
    assert "4" in capture.final_text, (
        f"Expected '4' in final response. Got: {capture.final_text!r}"
    )


# ─── Tareas complejas: tool calls ────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_thinking_with_moodle_list_courses(tg: TelegramTestClient) -> None:
    """
    Tarea compleja: listar cursos de Moodle requiere tool call.
    El thinking bubble debe funcionar igual: separado, sellado, sin leak.

    Escenario: thinking → tool_request → tool_response → body final.
    """
    capture = await tg.send_prompt(
        "Lista mis cursos en Moodle.",
        timeout=90,
        stable_window=4.0,
    )
    assert_not_empty(capture)
    # Con tool call, debe haber al menos thinking (si viene) + tool card + respuesta
    assert capture.final_messages, "No final response after Moodle tool call"
    if capture.thinking_message:
        assert_thinking_separated(capture)
        assert_thinking_not_in_final(capture)
        assert_thinking_max_length(capture, max_chars=800)
    # El body final debe ser HTML válido
    for msg in capture.final_messages:
        assert_message_length(msg.final_text)


@pytest.mark.e2e_telegram
async def test_thinking_with_calendar_list_events(tg: TelegramTestClient) -> None:
    """
    Tarea compleja: listar eventos del Google Calendar requiere tool call.
    Verifica el ciclo thinking → tool → respuesta con burbuja separada.
    """
    capture = await tg.send_prompt(
        "¿Qué eventos tengo en mi Google Calendar esta semana?",
        timeout=90,
        stable_window=4.0,
    )
    assert_not_empty(capture)
    assert capture.final_messages, "No final response after Calendar tool call"
    if capture.thinking_message:
        assert_thinking_separated(capture)
        assert_thinking_not_in_final(capture)
        assert_thinking_max_length(capture, max_chars=800)
    for msg in capture.final_messages:
        assert_message_length(msg.final_text)


@pytest.mark.e2e_telegram
async def test_thinking_with_combined_moodle_and_calendar(
    tg: TelegramTestClient,
) -> None:
    """
    Tarea combinada: listar cursos Moodle + eventos Calendar en una sola consulta.
    Requiere razonamiento multi-step y múltiples tool calls.

    Verifica:
    - Al menos 2 mensajes (thinking + respuesta, o tool cards + respuesta)
    - Thinking separado si presente
    - Sin thinking en body final
    """
    capture = await tg.send_prompt(
        "Dame un resumen rápido: ¿cuáles son mis cursos en Moodle "
        "y qué eventos tengo en los próximos 7 días en Google Calendar?",
        timeout=120,
        stable_window=4.0,
    )
    assert_not_empty(capture)
    assert capture.final_messages, "No final response after combined task"

    # Con thinking + al menos un tool call → debe haber ≥ 2 mensajes distintos
    assert len(capture.messages) >= 2, (
        f"Expected ≥2 messages (thinking + tool cards + response), "
        f"got {len(capture.messages)}: {capture.summary()}"
    )

    if capture.thinking_message:
        assert_thinking_separated(capture)
        assert_thinking_not_in_final(capture)
        assert_thinking_max_length(capture, max_chars=800)

    for msg in capture.final_messages:
        assert_message_length(msg.final_text)


# ─── Escenario de thinking largo ─────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_thinking_long_reasoning_truncated(tg: TelegramTestClient) -> None:
    """
    Fuerza un razonamiento largo con una tarea multi-step compleja.
    El thinking bubble debe estar truncado a ≤ 800 chars y el cuerpo debe
    llegar en mensaje separado con contenido útil.
    """
    capture = await tg.send_prompt(
        "Necesito que hagas un análisis completo: "
        "(1) Explicá la diferencia entre BFS y DFS con ejemplos concretos, "
        "(2) Indicá en qué situaciones conviene usar cada uno, "
        "(3) Dame la complejidad temporal y espacial de ambos. "
        "Razoná cada punto en detalle antes de responder.",
        timeout=60,
    )
    assert_not_empty(capture)
    assert_thinking_max_length(capture, max_chars=800)
    if capture.thinking_message:
        assert_thinking_separated(capture)
        assert_thinking_not_in_final(capture)
    assert capture.final_messages, "No final response for complex multi-part query"
    # Con una tarea tan larga, la respuesta debe ser sustancial
    final_text = capture.final_text
    assert len(final_text) > 100, (
        f"Final response too short ({len(final_text)} chars) for a complex query"
    )

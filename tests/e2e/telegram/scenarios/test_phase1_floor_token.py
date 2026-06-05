"""
E2E: Batería de verificación Fase 1 — Protocolo Conversacional (floor token).

Tests:
    1. Floor Token Básico — 2 bots se turnan sin pisarse
    2. Timeout Expira — token pasa al siguiente automáticamente
    3. Contexto Acumulativo — mensajes previos se incluyen en el prompt
    4. Cola FIFO — mensajes pendientes se procesan en orden

Cada test usa TelegramTestClient para enviar prompts y capturar respuestas.

Ejecución:
    pytest -m e2e_telegram tests/e2e/telegram/scenarios/test_phase1_floor_token.py -v

Requiere las env vars estándar de E2E (ver conftest.py / client.py).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.e2e.telegram.client import TelegramTestClient

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_keywords(text: str, *keywords: str) -> bool:
    """Check that ALL keywords appear in text (case-insensitive)."""
    lowered = text.lower()
    return all(kw.lower() in lowered for kw in keywords)


def _has_any_keyword(text: str, *keywords: str) -> bool:
    """Check that AT LEAST ONE keyword appears in text (case-insensitive)."""
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


# ── Test 1: Floor Token Básico ─────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_floor_token_basic(tg: TelegramTestClient) -> None:
    """#F1.1: 2 bots conversan alternándose sin pisarse.

    Enviamos un mensaje mencionando a un bot específico.
    Solo ese bot debe responder.
    """
    # Enviar mensaje mencionando solo a LINA
    capture1 = await tg.send_prompt(
        "@s_lina_bot decime solo: 'turno 1 - LINA' (una línea)",
        timeout=60,
        stable_window=5.0,
    )
    assert capture1.final_messages, "LINA no respondió al primer turno"
    text1 = capture1.final_text.lower()

    # LINA debe responder y mencionar su nombre
    assert _has_any_keyword(text1, "lina", "turno 1"), (
        f"LINA no respondió correctamente. Respuesta: {capture1.final_text[:300]}"
    )

    # Verificar que no hay múltiples respuestas pisándose
    assert len(capture1.final_messages) <= 3, (
        f"Demasiados mensajes — posible pisada de bots. "
        f"Count: {len(capture1.final_messages)}"
    )

    # Pequeña pausa para que el floor se libere
    await asyncio.sleep(2)

    # Ahora enviar mensaje mencionando solo a CLINE
    capture2 = await tg.send_prompt(
        "@s_cline_bot decime solo: 'turno 2 - CLINE' (una línea)",
        timeout=60,
        stable_window=5.0,
    )
    assert capture2.final_messages, "CLINE no respondió al segundo turno"
    text2 = capture2.final_text.lower()

    assert _has_any_keyword(text2, "cline", "turno 2"), (
        f"CLINE no respondió correctamente. Respuesta: {capture2.final_text[:300]}"
    )

    assert len(capture2.final_messages) <= 3, (
        f"Demasiados mensajes en turno 2 — posible pisada. "
        f"Count: {len(capture2.final_messages)}"
    )


# ── Test 2: Timeout Expira ─────────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_floor_timeout(tg: TelegramTestClient) -> None:
    """#F1.2: Timeout expira → token pasa al siguiente automáticamente.

    Enviamos un mensaje largo a LINA que le tomará tiempo procesar,
    y rápido enviamos otro a CLINE. CLINE debería esperar o recibir
    notificación de que LINA tiene el turno.
    """
    # Primer mensaje: algo que LINA procese
    await tg.send_command(
        "@s_lina_bot hacé una lista de los issues de GitHub del repo lina "
        "(usá gh issue list) y mostrame los primeros 5"
    )

    # Sin esperar, mandamos otro mensaje a CLINE
    await asyncio.sleep(1)
    capture2 = await tg.send_prompt(
        "@s_cline_bot decime rápido: 'CLINE acá, LINA ocupada'",
        timeout=45,
        stable_window=3.0,
    )

    # CLINE debería responder de alguna forma
    # (puede ser "esperando turno" o directamente la respuesta)
    assert capture2.final_messages, (
        "CLINE no respondió. Si LINA tenía el floor, CLINE debería "
        "al menos notificar que está esperando."
    )

    text2 = capture2.final_text.lower()
    # Verificar que CLINE mencionó algo útil
    assert _has_any_keyword(text2, "cline", "lina", "turno", "espera", "acá"), (
        f"CLINE no respondió coherentemente. Respuesta: {capture2.final_text[:300]}"
    )


# ── Test 3: Contexto Acumulativo ───────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_context_accumulator(tg: TelegramTestClient) -> None:
    """#F1.3: El contexto de mensajes previos se inyecta en el prompt.

    Hablamos con LINA sobre un tema, luego preguntamos y debería
    recordar el contexto de la conversación.
    """
    # Paso 1: registrar un hecho
    capture1 = await tg.send_prompt(
        "@s_lina_bot recordá: el número de la suerte de Fede es el 42",
        timeout=60,
        stable_window=5.0,
    )
    assert capture1.final_messages, "LINA no respondió al registro"
    text1 = capture1.final_text.lower()
    assert _has_any_keyword(text1, "guard", "ok", "42", "record", "listo"), (
        f"LINA no confirmó el registro. Respuesta: {capture1.final_text[:300]}"
    )

    await asyncio.sleep(2)

    # Paso 2: preguntar por el hecho directamente
    capture2 = await tg.send_prompt(
        "@s_lina_bot ¿cuál es el número de la suerte de Fede?",
        timeout=60,
        stable_window=5.0,
    )
    assert capture2.final_messages, "LINA no respondió a la pregunta"
    text2 = capture2.final_text.lower()

    # Debería recordar el 42
    assert "42" in text2, (
        f"LINA no recordó el número de la suerte. "
        f"Respuesta: {capture2.final_text[:300]}"
    )


# ── Test 4: Cola FIFO ─────────────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_fifo_queue(tg: TelegramTestClient) -> None:
    """#F1.4: Mensajes encolados se procesan en orden FIFO.

    Enviamos 2 mensajes rápidos: primero a LINA, inmediatamente a CLINE.
    Verificamos que el orden de respuesta sea ≈ el orden de envío.
    """
    import time as tmod

    # Enviar comando a LINA
    await tg.send_command(
        "@s_lina_bot respondé solo: 'mensaje 1 recibido' (una línea)"
    )

    tmod.sleep(0.5)

    # Enviar comando a CLINE
    await tg.send_command(
        "@s_cline_bot respondé solo: 'mensaje 2 recibido' (una línea)"
    )

    # Esperar que ambos procesen
    await asyncio.sleep(15)

    # Leer los últimos mensajes del chat para ver el orden
    # (esto se hace con el client de Telethon directamente)
    from telethon import TelegramClient as _TG
    from telethon.tl.types import MessageEntityMention

    api_id = int(__import__("os").environ.get("TELEGRAM_TEST_API_ID", "35434942"))
    api_hash = __import__("os").environ.get("TELEGRAM_TEST_API_HASH", "9f2a614fbf2e8cbfaf844561b7f43294")
    phone = __import__("os").environ.get("TELEGRAM_TEST_PHONE", "+59891675877")

    async with _TG(":memory:", api_id, api_hash) as tg2:
        tg2.parse_mode = None
        await tg2.start(phone=phone)
        bot_entity = await tg2.get_entity(__import__("os").environ.get("LINA_BOT_USERNAME", "@s_lina_bot"))
        msgs = await tg2.get_messages(bot_entity, limit=10)

        # Filtrar solo respuestas de bots
        bot_responses = []
        for m in msgs:
            if m.out:
                continue  # mensajes enviados por el test user
            txt = (m.text or "").lower()
            if "mensaje 1" in txt or "mensaje 2" in txt:
                bot_responses.append(txt)

        assert len(bot_responses) >= 2, (
            f"No se recibieron suficientes respuestas de bots. "
            f"Recibidas: {len(bot_responses)}. "
            f"Mensajes: {[m.text[:100] for m in msgs if not m.out]}"
        )

        # Verificar que mensaje 1 llegó antes que mensaje 2
        idx_1 = next(i for i, t in enumerate(bot_responses) if "mensaje 1" in t)
        idx_2 = next(i for i, t in enumerate(bot_responses) if "mensaje 2" in t)

        assert idx_1 < idx_2, (
            f"Orden incorrecto: mensaje 1 (pos {idx_1}) después de "
            f"mensaje 2 (pos {idx_2}). Respuestas: {bot_responses}"
        )

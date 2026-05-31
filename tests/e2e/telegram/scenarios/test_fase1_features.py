"""
E2E: Batería de verificación de features Fase 1 (hardening operacional).

Tests:
    1. #60 Smart Context Injection — recordar y recuperar datos del usuario
    2. #61 Token Metering — reporte de tokens, costo y balance
    3. #62 Reasoning Trace Persistence — recuperar último trace de la DB
    4. #63 pgvector Semantic Memory — guardar y buscar memoria semántica

Cada test envía prompts independientes a LINA vía Telegram y evalúa
acceptance criteria binarios (PASS/FAIL).

Ejecución:
    pytest -m e2e_telegram tests/e2e/telegram/scenarios/test_fase1_features.py -v

Requiere las env vars estándar de E2E (ver conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

import pytest

from tests.e2e.telegram.client import TelegramTestClient


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_keywords(text: str, *keywords: str) -> bool:
    """Check that ALL keywords appear in text (case-insensitive)."""
    lowered = text.lower()
    return all(kw.lower() in lowered for kw in keywords)


def _has_any_keyword(text: str, *keywords: str) -> bool:
    """Check that AT LEAST ONE keyword appears in text (case-insensitive)."""
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def _extract_numbers(text: str) -> list[float]:
    """Extract all numeric values (int or float) from text."""
    return [float(m) for m in re.findall(r"[\d,]+\.?\d*", text.replace(",", ""))]


# ── Test 1: #60 Smart Context Injection ─────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_smart_context_injection(tg: TelegramTestClient) -> None:
    """#60: LINA recuerda datos personales entre turnos de una misma sesión."""
    # Paso 1: registrar datos
    capture1 = await tg.send_prompt(
        "Recordá esto: mi color favorito es verde azulado y estoy leyendo "
        '"Clean Architecture" de Uncle Bob. Confirmame que lo guardaste.',
        timeout=60,
        stable_window=5.0,
    )
    assert capture1.final_messages, "LINA no respondió al prompt de registro"
    text1 = capture1.final_text.lower()
    assert _has_any_keyword(text1, "guard", "guardé", "guardado", "listo", "ok", "registrado", "anotado", "recordado"), (
        f"LINA no confirmó el guardado. Respuesta: {capture1.final_text[:300]}"
    )

    # Pequeña pausa para que la memoria se persista
    await asyncio.sleep(1)

    # Paso 2: preguntar por los datos
    capture2 = await tg.send_prompt(
        "¿Cuál es mi color favorito y qué libro estoy leyendo?",
        timeout=60,
        stable_window=5.0,
    )
    assert capture2.final_messages, "LINA no respondió a la pregunta de recall"
    text2 = capture2.final_text.lower()

    color_ok = "verde azulado" in text2 or "azulado" in text2 or "verde" in text2
    libro_ok = "clean architecture" in text2 or "uncle bob" in text2

    assert color_ok, (
        f"LINA no recordó el color favorito. Respuesta: {capture2.final_text[:300]}"
    )
    assert libro_ok, (
        f"LINA no recordó el libro. Respuesta: {capture2.final_text[:300]}"
    )


# ── Test 2: #61 Token Metering ──────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_token_metering_report(tg: TelegramTestClient) -> None:
    """#61: LINA puede reportar tokens, costo y balance de DeepSeek."""
    capture = await tg.send_prompt(
        "Usá las tools get_session_cost, get_daily_cost y get_balance para "
        "darme un reporte completo de consumo. Necesito:\n"
        "1. Tokens prompt y output de esta sesión\n"
        "2. Costo estimado en USD\n"
        "3. Balance actual de DeepSeek\n"
        "Respondé con los números concretos, no con texto genérico.",
        timeout=90,
        stable_window=10.0,
    )
    assert capture.final_messages, "LINA no respondió al prompt de metering"
    text = capture.final_text.lower()

    # Debe mencionar alguno de estos conceptos
    has_tokens = _has_any_keyword(text, "token", "prompt", "output", "completion")
    has_cost = _has_any_keyword(text, "usd", "costo", "cost", "$", "balance")

    # Extraer números para verificar que no son todos cero
    numbers = _extract_numbers(capture.final_text)

    assert has_tokens, (
        f"LINA no mencionó tokens. Respuesta: {capture.final_text[:400]}"
    )
    assert has_cost, (
        f"LINA no mencionó costo/balance. Respuesta: {capture.final_text[:400]}"
    )
    assert len(numbers) >= 2, (
        f"Se esperaban al menos 2 valores numéricos (tokens + costo/balance). "
        f"Encontrados: {numbers}. Respuesta: {capture.final_text[:400]}"
    )

    # Al menos un número debe ser > 0 (no todo en cero)
    has_positive = any(n > 0 for n in numbers)
    assert has_positive, (
        f"Todos los valores numéricos son cero. Numbers={numbers}. "
        f"Respuesta: {capture.final_text[:400]}"
    )


# ── Test 3: #62 Reasoning Trace Persistence ─────────────────────────────────

@pytest.mark.e2e_telegram
async def test_reasoning_trace_persistence(tg: TelegramTestClient) -> None:
    """#62: LINA puede recuperar su último reasoning trace de la DB."""
    capture = await tg.send_prompt(
        "Usá la tool list_reasoning_traces con limit=1 para recuperar tu "
        "último trace de razonamiento. Mostrame:\n"
        "- session_id\n"
        "- turn_number\n"
        "- model\n"
        "- created_at\n"
        "- El texto del thinking (al menos los primeros 200 caracteres)\n"
        "Respondé con los datos concretos.",
        timeout=90,
        stable_window=10.0,
    )
    assert capture.final_messages, "LINA no respondió al prompt de reasoning trace"
    text = capture.final_text

    # Verificar presencia de campos esperados
    has_session = bool(re.search(r"session[_\s]?id", text, re.IGNORECASE)) or bool(
        re.search(r"2026\d{4}[_\s]?\d+", text)
    )
    has_model = _has_any_keyword(
        text, "deepseek", "model", "modelo", "v4", "v3", "reasoner", "chat"
    )
    has_turn = bool(re.search(r"turn[_\s]?(number|num|\d+)", text, re.IGNORECASE))
    has_timestamp = bool(re.search(r"2026-\d{2}-\d{2}", text)) or bool(
        re.search(r"created", text, re.IGNORECASE)
    )

    # El thinking debe tener al menos 50 caracteres de contenido real
    # (descontamos el encabezado del mensaje)
    thinking_ok = len(text) > 200  # Respuesta completa con thinking debe ser larga

    assert has_session, (
        f"No se encontró session_id en la respuesta. Respuesta: {text[:400]}"
    )
    assert has_model, (
        f"No se encontró referencia al modelo. Respuesta: {text[:400]}"
    )
    assert has_turn, (
        f"No se encontró turn_number. Respuesta: {text[:400]}"
    )
    assert has_timestamp, (
        f"No se encontró timestamp/created_at. Respuesta: {text[:400]}"
    )
    assert thinking_ok, (
        f"Respuesta demasiado corta para contener un thinking trace "
        f"({len(text)} chars). Respuesta: {text[:400]}"
    )

    # Verificar que no sea un error
    assert not _has_any_keyword(
        text, "no data", "sin datos", "vacío", "empty", "error", "no tengo acceso"
    ), f"LINA reportó error o falta de datos: {text[:400]}"


# ── Test 4: #63 pgvector Semantic Memory ────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_pgvector_semantic_memory(tg: TelegramTestClient) -> None:
    """#63: LINA puede guardar y recuperar memorias semánticas con pgvector."""
    # Paso 1: guardar memoria semántica
    capture1 = await tg.send_prompt(
        "Usá la tool store_semantic_memory para guardar esto: "
        '"A Federico le gusta programar en Rust porque es rápido y seguro". '
        "Confirmame que se guardó correctamente.",
        timeout=60,
        stable_window=5.0,
    )
    assert capture1.final_messages, "LINA no respondió al prompt de store semántico"
    text1 = capture1.final_text.lower()
    assert _has_any_keyword(text1, "guard", "ok", "guardado", "listo", "correct", "éxito", "success", "created", "updated", "insertado"), (
        f"LINA no confirmó el guardado semántico. Respuesta: {capture1.final_text[:300]}"
    )

    await asyncio.sleep(1)

    # Paso 2: buscar memoria semántica
    capture2 = await tg.send_prompt(
        "Usá la tool search_semantic_memory para buscar: "
        '"qué lenguaje de programación prefiere Fede?". '
        "Mostrame los resultados.",
        timeout=60,
        stable_window=5.0,
    )
    assert capture2.final_messages, "LINA no respondió al prompt de búsqueda semántica"
    text2 = capture2.final_text.lower()

    # Debe mencionar Rust y algo sobre rápido/seguro
    found_rust = "rust" in text2
    found_quality = _has_any_keyword(text2, "rápido", "rapido", "seguro", "safe", "fast")

    assert found_rust, (
        f"LINA no encontró 'Rust' en la búsqueda semántica. "
        f"Respuesta: {capture2.final_text[:400]}"
    )
    assert found_quality, (
        f"LINA no mencionó 'rápido y seguro' en la búsqueda semántica. "
        f"Respuesta: {capture2.final_text[:400]}"
    )

    # No debe reportar error
    assert not _has_any_keyword(
        text2, "no encontré nada", "no tengo acceso", "error", "falló", "sin resultados", "0 resultados"
    ), f"LINA reportó error en búsqueda semántica: {capture2.final_text[:400]}"


# ── Reporte final ───────────────────────────────────────────────────────────

def _build_report(results: list[dict]) -> str:
    """Build a JSON report from test results."""
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "results": results,
    }
    return json.dumps(report, indent=2, ensure_ascii=False)
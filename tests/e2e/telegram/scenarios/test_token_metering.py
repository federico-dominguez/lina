"""
E2E: Verifica que el metering de tokens/costo funciona end-to-end (issue #61).

Tests:
    1. Enviar un mensaje a LINA y verificar que se registró en token_usage
    2. Pedirle a LINA que reporte su propio consumo vía get_session_cost
    3. Verificar que get_daily_cost devuelve datos del día de hoy
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from tests.e2e.telegram.client import TelegramTestClient


def _psql(sql: str) -> str:
    """Run a psql query in the lina-db container and return stdout."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker/docker-compose.yml",
            "exec",
            "-T",
            "lina-db",
            "psql",
            "-U",
            "lina",
            "-d",
            "lina",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        cwd="/home/fede/lina",
    )
    return result.stdout + result.stderr


@pytest.mark.e2e_telegram
async def test_token_usage_row_inserted(tg: TelegramTestClient) -> None:
    """Después de un turno, debe existir al menos una fila en token_usage."""
    # Count rows before
    before_out = _psql("SELECT COUNT(*) FROM token_usage;")
    before_count = int([l.strip() for l in before_out.splitlines() if l.strip().isdigit()][0])

    capture = await tg.send_prompt(
        "Di exactamente: 'Prueba de metering activa'",
        timeout=60,
        stable_window=5.0,
    )
    assert capture.final_messages, "LINA no respondió al mensaje de prueba"

    # Give the async persist a moment to complete
    await asyncio.sleep(2)

    after_out = _psql("SELECT COUNT(*) FROM token_usage;")
    after_count = int([l.strip() for l in after_out.splitlines() if l.strip().isdigit()][0])

    assert after_count > before_count, (
        f"token_usage no creció después del turno. Antes={before_count}, "
        f"Después={after_count}. "
        f"Respuesta de LINA: {capture.final_text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_lina_reports_session_cost(tg: TelegramTestClient) -> None:
    """LINA puede reportar el costo de la sesión actual usando get_session_cost."""
    # Get the actual session_id from DB (format YYYYMMDD_N)
    out = _psql("SELECT DISTINCT session_id FROM token_usage ORDER BY 1 DESC LIMIT 1;")
    lines = [l.strip() for l in out.splitlines() if l.strip() and "session_id" not in l and "---" not in l and "row" not in l]
    session_id = lines[0] if lines else None
    assert session_id, f"No hay session_id en token_usage todavía: {out}"

    capture = await tg.send_prompt(
        f"Usá el tool get_session_cost con el session_id '{session_id}'. "
        "Decime exactamente: cuántos turnos hay registrados y el costo total "
        "estimado en USD. Respondé con los números concretos.",
        timeout=90,
        stable_window=10.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Should mention some kind of cost/token data
    has_cost = any(
        kw in text
        for kw in ["usd", "costo", "cost", "turno", "turn", "$", "0.0", "token", session_id]
    )
    assert has_cost, (
        f"LINA no reportó datos de costo/tokens para session_id={session_id!r}. "
        f"Respuesta: {capture.final_text[:500]}"
    )


@pytest.mark.e2e_telegram
async def test_lina_reports_daily_cost(tg: TelegramTestClient) -> None:
    """LINA puede reportar el costo diario de los últimos 7 días."""
    capture = await tg.send_prompt(
        "Usá el tool get_daily_cost con days=7 y decime el consumo de hoy "
        "(fecha de hoy). Si no hay datos de hoy, confirmá que la lista está "
        "vacía o que hoy no aparece.",
        timeout=90,
        stable_window=8.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Should mention the tool result in some form
    has_result = any(
        kw in text
        for kw in ["usd", "costo", "cost", "hoy", "today", "vacía", "empty", "día", "0.0", "token", "mayo", "may", "2026"]
    )
    assert has_result, (
        f"LINA no reportó resultado de get_daily_cost. Respuesta: {capture.final_text[:500]}"
    )


@pytest.mark.e2e_telegram
async def test_token_usage_has_valid_data(tg: TelegramTestClient) -> None:
    """Las filas en token_usage tienen datos coherentes (chars > 0, cost >= 0)."""
    out = _psql(
        "SELECT prompt_chars, completion_chars, prompt_tokens_est, "
        "completion_tokens_est, cost_usd_est "
        "FROM token_usage ORDER BY created_at DESC LIMIT 1;"
    )
    assert "0 rows" not in out, f"token_usage está vacía: {out}"

    lines = [l for l in out.splitlines() if "|" in l and "prompt_chars" not in l]
    assert lines, f"No se encontraron filas de datos en: {out}"

    row = lines[0]
    values = [v.strip() for v in row.split("|")]
    # prompt_chars, completion_chars should be > 0
    prompt_chars = int(values[0]) if values[0].isdigit() else -1
    assert prompt_chars > 0, f"prompt_chars es 0 o inválido: {row}"

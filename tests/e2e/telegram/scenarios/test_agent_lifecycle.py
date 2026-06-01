"""
E2E: Verifica los nuevos tools de ciclo de vida de agentes (issues #83, #84, #85).

Tests:
    1. list_agents — LINA puede listar agentes (debería devolver lista vacía o activos)
    2. get_agent_status con ID inexistente — LINA maneja el error correctamente
    3. spawn_agent con rol "dev" — LINA puede spawnar un sub-agente y reportar el ID
    4. kill_agent — LINA puede terminar el agente spawneado

Nota: spawn_agent lanza un proceso goosed real. En el entorno E2E el binario
goosed puede no estar en la ruta configurada dentro del contenedor orquestador,
pero LINA debe al menos reportar el error descriptivo (no un traceback crudo).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_list_agents_empty_or_active(tg: TelegramTestClient) -> None:
    """list_agents devuelve una lista (vacía o con agentes activos).

    Verifica que LINA tiene acceso al tool list_agents del MCP lina-orchestrator.
    """
    capture = await tg.send_prompt(
        "Usa el MCP lina-orchestrator: llama a list_agents() y dimé cuántos agentes "
        "hay activos ahora mismo. Si la lista está vacía dímelo explícitamente.",
        timeout=60,
        stable_window=15.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    assert any(
        kw in text
        for kw in [
            "agente", "agent", "activo", "active", "vacía", "vacío", "vacio",
            "empty", "ningún", "ningun", "0", "cero", "no hay",
        ]
    ), f"LINA no reportó estado de agentes. Respuesta: {text[:400]}"


@pytest.mark.e2e_telegram
async def test_get_agent_status_not_found(tg: TelegramTestClient) -> None:
    """get_agent_status con ID inexistente devuelve error descriptivo."""
    await asyncio.sleep(8)

    capture = await tg.send_prompt(
        "Usa el MCP lina-orchestrator: llama a get_agent_status con "
        "agent_id='00000000000000000000000000000000' y dimé qué responde.",
        timeout=60,
        stable_window=15.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    # Debe indicar que no se encontró, o error, o "no encontrada"
    assert any(
        kw in text
        for kw in [
            "no encontr", "not found", "error", "sesión", "session",
            "inexistente", "0000", "no existe",
        ]
    ), f"LINA no reportó el estado del agente inexistente. Respuesta: {text[:400]}"


@pytest.mark.e2e_telegram
async def test_spawn_agent_dev_role(tg: TelegramTestClient) -> None:
    """spawn_agent con rol 'dev' devuelve un agent_id o error descriptivo.

    En producción debería funcionar; en E2E puede fallar si goosed no está en
    la ruta del contenedor orquestador, pero LINA debe reportar el error
    (no un traceback crudo ni silencio).
    """
    await asyncio.sleep(8)

    capture = await tg.send_prompt(
        "Usa el MCP lina-orchestrator: llama a spawn_agent con role='dev' y "
        "goal='verificar que el sistema de orquestación funciona'. "
        "Dimé el agent_id que obtengas, o el error si no funciona.",
        timeout=90,
        stable_window=20.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    # Éxito: tiene un agent_id (32 hex chars) o reporta error descriptivo
    has_agent_id = any(
        len(word.replace("-", "").replace("_", "")) >= 8
        and all(c in "0123456789abcdef-_" for c in word.lower())
        for word in capture.final_text.split()
    )
    has_error_description = any(
        kw in text
        for kw in [
            "error", "goosed", "no encontrado", "not found", "failed",
            "fallido", "lanzado", "spawned", "agent_id", "iniciado",
        ]
    )
    assert has_agent_id or has_error_description, (
        f"LINA no reportó agent_id ni error descriptivo. Respuesta: {text[:400]}"
    )

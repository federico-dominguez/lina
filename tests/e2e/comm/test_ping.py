"""
test_ping — Smoke tests E2E vía Comm.

Tests básicos que verifican que cada bot del ecosistema
responde correctamente a un "ping" vía el sistema Comm.

Ejecución:
    pytest tests/e2e/comm/test_ping.py -v
    pytest tests/e2e/comm/test_ping.py -v -k lina     # solo LINA
    pytest tests/e2e/comm/test_ping.py -v -k goose    # solo Goose
"""

from __future__ import annotations

import logging

import pytest

from .comm_harness import CommTestClient

logger = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _ping_bot(
    comm: CommTestClient,
    bot: str,
    *,
    timeout: float = 60.0,
    silence: float = 6.0,
) -> None:
    """Envía 'ping' a un bot y verifica que responda.

    Args:
        comm: Cliente Comm autenticado
        bot: Nombre del bot ('lina', 'goose', 'cline', 'gemma')
        timeout: Timeout para este bot específico
        silence: Ventana de silencio para considerar respuesta completa
    """
    logger.info("🔍 Test: ping a %s...", bot)

    resp = await comm.send_and_wait(
        bot,
        "respondé solo: OK (un renglón)",
        timeout=timeout,
        silence=silence,
    )

    # Verificaciones
    assert resp.messages, (
        f"❌ {bot} no respondió al ping. "
        f"Revisá que {bot} esté activo en el grupo Comm."
    )
    assert resp.contains("OK"), (
        f"❌ {bot} respondió pero no dijo 'OK'. "
        f"Respuesta: {resp.text[:200]}"
    )
    assert resp.ttft is not None and resp.ttft < timeout, (
        f"❌ {bot} tardó demasiado en responder: TTFT={resp.ttft:.1f}s"
    )

    logger.info("✅ %s OK — TTFT=%.1fs TTLT=%.1fs",
                bot, resp.ttft or 0, resp.ttlt or 0)


# ─── Smoke Tests Individuales ────────────────────────────────────────────────


@pytest.mark.e2e_comm
@pytest.mark.smoke
async def test_ping_lina(comm: CommTestClient) -> None:
    """#SMOKE-01: LINA responde al ping vía Comm.
    
    Envía 'ping' a @s_lina_bot y verifica que responda con 'OK'.
    Tiempo esperado: < 30s
    """
    await _ping_bot(comm, "lina", timeout=45, silence=6.0)


@pytest.mark.e2e_comm
@pytest.mark.smoke
async def test_ping_goose(comm: CommTestClient) -> None:
    """#SMOKE-02: Goose responde al ping vía Comm.
    
    Envía 'ping' a @s_goose_bot y verifica que responda con 'OK'.
    Tiempo esperado: < 20s
    """
    await _ping_bot(comm, "goose", timeout=30, silence=5.0)


@pytest.mark.e2e_comm
@pytest.mark.smoke
async def test_ping_cline(comm: CommTestClient) -> None:
    """#SMOKE-03: Cline responde al ping vía Comm.
    
    Envía 'ping' a @s_cline_bot y verifica que responda con 'OK'.
    Tiempo esperado: < 30s
    """
    await _ping_bot(comm, "cline", timeout=45, silence=6.0)


@pytest.mark.e2e_comm
@pytest.mark.smoke
async def test_ping_gemma(comm: CommTestClient) -> None:
    """#SMOKE-04: Gemma responde al ping vía Comm.
    
    Envía 'ping' a @s_gemma_bot y verifica que responda con 'OK'.
    Tiempo esperado: < 30s
    """
    await _ping_bot(comm, "gemma", timeout=45, silence=6.0)


# ─── Test de Salud General ───────────────────────────────────────────────────


@pytest.mark.e2e_comm
@pytest.mark.smoke
async def test_comm_health(comm: CommTestClient) -> None:
    """#SMOKE-00: Verifica que el sistema Comm esté operativo.
    
    Este test no envía mensajes a ningún bot, solo verifica
    que el CommTestClient esté correctamente conectado.
    """
    health = await comm.health()
    assert health["connected"], "Cuenta Comm no conectada"
    assert health["group_id"] is not None, "Grupo Comm no detectado"
    assert health["bots_resolved"] >= 3, (
        f"Se resolvieron {health['bots_resolved']}/4 bots. "
        "Se esperaban al menos 3 (LINA, Goose, Cline)."
    )
    assert health["db_connected"], "Conexión a PostgreSQL falló"

    logger.info("✅ Salud Comm OK:")
    logger.info("  Cuenta: %s", health["me"])
    logger.info("  Grupo: %s (ID=%s)", health["group"], health["group_id"])
    logger.info("  Bots: %s", health["bots"])
    logger.info("  DB: %s", "✅ conectada" if health["db_connected"] else "❌")

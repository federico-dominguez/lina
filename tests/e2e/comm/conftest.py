"""
conftest.py — Fixtures pytest para tests E2E vía Comm.

Provee el fixture `comm` que es un CommTestClient autenticado y listo.
Los tests solo necesitan declararlo como parámetro:

    async def test_algo(comm):
        resp = await comm.send_and_wait("lina", "Hola!")
        assert resp.contains("Hola")
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .comm_harness import CommTestClient


def pytest_addoption(parser):
    parser.addoption(
        "--comm-timeout",
        type=float,
        default=float(os.environ.get("COMM_E2E_TIMEOUT", "120")),
        help="Timeout máximo para esperar respuesta del bot (segundos)",
    )
    parser.addoption(
        "--comm-silence",
        type=float,
        default=float(os.environ.get("COMM_E2E_SILENCE", "8.0")),
        help="Segundos de silencio para considerar respuesta completa",
    )
    parser.addoption(
        "--comm-skip-slow",
        action="store_true",
        default=True,
        help="Saltar tests marcados como slow (default: True)",
    )


@pytest.fixture(scope="session")
def comm_timeout(request) -> float:
    """Timeout personalizable por sesión. Default: 120s."""
    return request.config.getoption("--comm-timeout")


@pytest.fixture(scope="session")
def comm_silence(request) -> float:
    """Ventana de silencio personalizable. Default: 8s."""
    return request.config.getoption("--comm-silence")


@pytest.fixture(scope="session")
async def comm(comm_timeout: float, comm_silence: float) -> CommTestClient:
    """Fixture principal: CommTestClient autenticado y listo.

    Se conecta como la cuenta Comm (+59891992356), detecta el grupo,
    resuelve los IDs de los bots, y se desconecta al finalizar la sesión.

    Uso:
        async def test_ping(comm):
            resp = await comm.send_and_wait("lina", "ping")
            assert not resp.is_empty()"""
    client = CommTestClient(
        timeout=comm_timeout,
        silence=comm_silence,
    )
    await client.start()

    # Verificar salud
    health = await client.health()
    assert health["connected"], "No se pudo conectar la cuenta Comm"
    assert health["group_id"] is not None, (
        "No se detectó el grupo Comm. "
        "Asegurate de que la cuenta Comm (+59891992356) esté en el grupo."
    )
    assert health["bots_resolved"] > 0, (
        "No se pudieron resolver los @usernames de los bots. "
        "¿Están los bots en el grupo?"
    )

    yield client

    await client.stop()


@pytest.fixture(autouse=True)
async def _ensure_db_clean(request, comm: CommTestClient):
    """Auto-fixture: limpia mensajes de test anteriores antes de cada test."""
    yield
    # No limpiamos nada — los mensajes de test son útiles para debugging

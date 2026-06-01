"""Smoke test del prototipo Fase A.

No ejecuta el grafo (requeriría OPENAI_API_KEY y MCP servers reales). Solo
verifica que el módulo importa y que las WorkerSpecs son válidas. La validación
funcional del prototipo es manual y documentada en el ADR 0010.
"""

from __future__ import annotations


def test_module_imports() -> None:
    from application.orchestration import prototype

    assert callable(prototype.main)
    assert callable(prototype.manager_orchestrate)
    assert callable(prototype.run_worker)


def test_worker_specs_are_consistent() -> None:
    from application.orchestration.prototype import WORKERS

    assert set(WORKERS) == {"dev", "study"}
    for role, spec in WORKERS.items():
        assert spec.role == role
        assert spec.name and spec.instruction
        assert spec.server_names, f"{role} debe tener al menos un MCP allowlisted"


def test_dev_and_study_have_disjoint_mcps() -> None:
    """Política de aislamiento: dev no toca web, study no toca fs."""
    from application.orchestration.prototype import WORKERS

    dev_mcps = set(WORKERS["dev"].server_names)
    study_mcps = set(WORKERS["study"].server_names)
    assert dev_mcps.isdisjoint(study_mcps), (
        f"Workers de roles distintos no deben compartir MCPs: dev={dev_mcps} study={study_mcps}"
    )

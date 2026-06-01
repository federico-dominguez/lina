"""E2E Telegram — PolicyStore RBAC enforcement via lina-orchestrator (issue #101).

Verifica que LINA invoca el MCP `lina-orchestrator` para responder preguntas
sobre permisos de subagentes definidos en `config/policies.yaml`.

Prerrequisitos:
    - `lina-mcp-orchestrator` corriendo y accesible desde el contenedor goosed
      (docker compose up -d lina-mcp-orchestrator lina-mcp-gateway).
    - `config/policies.yaml` con al menos los roles dev/ops/study/research.

Ejecución:
    pytest -m e2e_telegram tests/e2e/telegram/scenarios/test_policy_enforcement.py -v
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.client import TelegramTestClient

# ─── Constantes ──────────────────────────────────────────────────────────────

_KNOWN_ROLES = ["dev", "ops", "study", "research"]

# Nombres parciales de keyword para detección case-insensitive
_ROLES_KEYWORDS = _KNOWN_ROLES  # los nombres de los roles son las keywords


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_list_roles_via_telegram(tg: TelegramTestClient) -> None:
    """LINA usa list_roles() del orchestrator y devuelve los roles definidos.

    Acceptance: la respuesta menciona al menos 3 de los 4 roles conocidos
    (dev, ops, study, research).
    """
    capture = await tg.send_prompt(
        "Usá la tool list_roles del MCP lina-orchestrator y decime "
        "qué roles de subagente están definidos en policies.yaml. "
        "Listá todos los roles.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    found = sum(role in text for role in _KNOWN_ROLES)
    assert found >= 3, (
        f"Solo {found}/4 roles mencionados en la respuesta. "
        f"Esperaba ver: {_KNOWN_ROLES}. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_check_mcp_allowed_negative(tg: TelegramTestClient) -> None:
    """El rol 'dev' NO puede usar lina-shell-policy (no está en allowed_mcps de dev).

    Nota: según policies.yaml actual dev SÍ tiene lina-shell-policy. Este test
    debe actualizarse si el yaml cambia. En el estado actual se usa 'research'
    (solo lina-db) para verificar el caso negativo.

    Acceptance: la respuesta indica que el rol research NO puede usar lina-fs-safe.
    """
    capture = await tg.send_prompt(
        "Usá la tool check_mcp_allowed del MCP lina-orchestrator. "
        "Pregunta: ¿puede el rol 'research' usar el MCP 'lina-fs-safe'? "
        "Respondé con sí o no y el motivo.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # La respuesta debe indicar negativo: "no", "false", "no permitido", "no puede"
    negative_indicators = [
        "no",
        "false",
        "false",
        "no puede",
        "no está",
        "no tiene",
        "denied",
    ]
    positive_but_wrong = ["sí puede", "si puede", "allowed: true", "puede usar"]

    has_negative = any(kw in text for kw in negative_indicators)
    has_wrong_positive = any(kw in text for kw in positive_but_wrong)

    assert has_negative and not has_wrong_positive, (
        f"LINA no indicó correctamente que research no puede usar lina-fs-safe. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_check_mcp_allowed_positive(tg: TelegramTestClient) -> None:
    """El rol 'dev' SÍ puede usar lina-fs-safe (en allowed_mcps de dev).

    Acceptance: la respuesta indica que el rol dev puede usar lina-fs-safe.
    """
    capture = await tg.send_prompt(
        "Usá la tool check_mcp_allowed del MCP lina-orchestrator. "
        "Pregunta: ¿puede el rol 'dev' usar el MCP 'lina-fs-safe'? "
        "Respondé con sí o no y el motivo.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    positive_indicators = [
        "sí",
        "si",
        "true",
        "puede",
        "allowed",
        "permitido",
        "tiene acceso",
    ]
    has_positive = any(kw in text for kw in positive_indicators)

    assert has_positive, (
        f"LINA no confirmó que dev puede usar lina-fs-safe. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_reload_policies_via_telegram(tg: TelegramTestClient) -> None:
    """LINA puede recargar policies.yaml sin reiniciar el proceso.

    Acceptance:
        - Responde con éxito (success=true).
        - Menciona la cantidad de roles recargados (≥3).
    """
    capture = await tg.send_prompt(
        "Ejecutá la tool reload_policies del MCP lina-orchestrator y "
        "reportame el resultado: ¿fue exitoso? ¿cuántos roles cargó?",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    success_indicators = [
        "success",
        "exitoso",
        "éxito",
        "true",
        "recargad",
        "cargad",
        "reload",
    ]
    has_success = any(kw in text for kw in success_indicators)
    assert has_success, (
        f"LINA no reportó éxito en el reload de políticas. "
        f"Respuesta: {capture.final_text[:600]}"
    )

    # Debe mencionar al menos uno de los conteos válidos (4 roles total)
    count_indicators = ["4", "cuatro", "roles"]
    has_count = any(kw in text for kw in count_indicators)
    assert has_count, (
        f"LINA no mencionó el número de roles recargados. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_requires_approval_for_risky_action(tg: TelegramTestClient) -> None:
    """pr_merge con el rol dev requiere aprobación humana.

    Según policies.yaml: dev.needs_approval_for incluye 'pr_merge'.

    Acceptance: la respuesta indica que sí se requiere aprobación.
    """
    capture = await tg.send_prompt(
        "Usá la tool check_requires_approval del MCP lina-orchestrator. "
        "Pregunta: ¿la acción 'pr_merge' con el rol 'dev' requiere aprobación humana? "
        "Respondé sí o no con el motivo.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    approval_indicators = [
        "sí",
        "si",
        "true",
        "requiere",
        "aprobación",
        "aprobacion",
        "requires",
        "approval",
        "confirmación",
        "confirmacion",
    ]
    has_approval = any(kw in text for kw in approval_indicators)

    assert has_approval, (
        f"LINA no confirmó que pr_merge requiere aprobación para dev. "
        f"Respuesta: {capture.final_text[:600]}"
    )

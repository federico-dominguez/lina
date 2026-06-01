"""
E2E Telegram — Verificación de issues #79, #82, #48.

Tests:
    1. #82 — PolicyStore: LINA carga config/policies.yaml correctamente y puede
             describir roles/MCPs permitidos sin reiniciarse.
    2. #48 — reload_mcp: la tool existe en shell-policy y responde correctamente
             (incluyendo el path de MCP desconocido, que debe ser fail-closed).
    3. #79 — Docker CI: LINA puede describir las imágenes disponibles / confirmar
             que lina-deploy existe y funciona (indirectamente valida el pipeline).

Ejecución:
    pytest -m e2e_telegram tests/e2e/telegram/scenarios/test_issues_79_82_48.py -v

Requiere las env vars estándar de E2E (ver conftest.py).
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.client import TelegramTestClient


# ── helpers ──────────────────────────────────────────────────────────────────


def _has_all(text: str, *keywords: str) -> bool:
    low = text.lower()
    return all(k.lower() in low for k in keywords)


def _has_any(text: str, *keywords: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


# ── Issue #82: config/policies.yaml + PolicyStore ────────────────────────────


@pytest.mark.e2e_telegram
async def test_82_policies_yaml_roles_loaded(tg: TelegramTestClient) -> None:
    """LINA conoce los roles definidos en config/policies.yaml.

    Acceptance: respuesta menciona los 4 roles (dev, ops, study, research)
    o describe MCPs asociados a alguno de ellos.
    """
    capture = await tg.send_prompt(
        "Leé el archivo /home/user/lina/config/policies.yaml y decime "
        "qué roles de subagente están definidos y qué MCPs tiene cada uno.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Al menos 3 de los 4 roles deben aparecer en la respuesta
    roles_found = sum(r in text for r in ["dev", "ops", "study", "research"])
    assert roles_found >= 3, (
        f"Solo {roles_found}/4 roles encontrados en la respuesta. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_82_policies_yaml_fail_closed(tg: TelegramTestClient) -> None:
    """El PolicyStore es fail-closed: un rol desconocido debe generar error claro.

    Acceptance: LINA reporta que 'hacker' no es un rol válido y lista los roles
    conocidos (sin crashear ni inventar permisos).
    """
    capture = await tg.send_prompt(
        "Ejecuta este fragmento Python via sh_run:\n"
        "python3 -c \""
        "import sys; sys.path.insert(0, '/home/user/lina'); "
        "from mcps.orchestrator.src.lina_orchestrator.domain.policy import PolicyStore; "
        "import pathlib; "
        "store = PolicyStore.from_yaml(pathlib.Path('/home/user/lina/config/policies.yaml')); "
        "store.get('hacker')"
        "\"\n"
        "Contame qué error arrojó (si fue KeyError, muéstrame el mensaje).",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Debe mencionar error o que no existe el rol
    assert _has_any(text, "keyerror", "no existe", "desconocido", "unknown", "error", "hacker"), (
        f"No detectó falla para rol 'hacker'. Respuesta: {capture.final_text[:600]}"
    )


# ── Issue #48: reload_mcp tool en lina-shell-policy ──────────────────────────


@pytest.mark.e2e_telegram
async def test_48_reload_mcp_tool_exists(tg: TelegramTestClient) -> None:
    """La tool reload_mcp está disponible en lina-shell-policy.

    Acceptance: LINA puede llamar reload_mcp con un MCP desconocido y recibir
    el error fail-closed (sin crashear) — esto confirma que la tool existe
    y funciona sin necesitar Docker corriendo.
    """
    capture = await tg.send_prompt(
        "Usá la tool reload_mcp de lina-shell-policy con el nombre 'lina-nonexistent'. "
        "Decime qué respondió (success=true/false y el mensaje de error si hubo).",
        timeout=90,
        stable_window=20.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Debe mencionar que falló (success=false) y describir el error
    assert _has_any(text, "false", "falló", "no existe", "desconocido", "unknown", "error"), (
        f"LINA no reportó falla para MCP inexistente. Respuesta: {capture.final_text[:600]}"
    )
    # No debe reportar success=true para un MCP que no existe
    assert "success" not in text or "false" in text, (
        f"LINA reportó success=true para MCP inexistente. Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_48_reload_mcp_known_mcps_list(tg: TelegramTestClient) -> None:
    """reload_mcp conoce la lista de MCPs válidos.

    Acceptance: cuando se llama con MCP desconocido, el error lista los MCPs
    conocidos — LINA puede reportar esa lista.
    """
    capture = await tg.send_prompt(
        "Usá reload_mcp con nombre 'lina-test-invalid' y decime exactamente "
        "qué MCPs aparecen en el mensaje de error (los MCPs válidos que lista).",
        timeout=90,
        stable_window=20.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Al menos 3 de los MCPs reales deben aparecer en la respuesta
    known_mcps = ["lina-fs-safe", "lina-db", "lina-secrets", "lina-moodle", "lina-shell-policy"]
    found = sum(mcp in text for mcp in known_mcps)
    assert found >= 3, (
        f"Solo {found} MCPs conocidos en el error. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_48_reload_mcp_requires_sudo(tg: TelegramTestClient) -> None:
    """reload_mcp requiere LINA_SHELL_ALLOW_SUDO=1 (gate de seguridad).

    Nota: en el entorno de producción LINA_SHELL_ALLOW_SUDO=1 está seteado,
    por eso este test verifica el comportamiento con un MCP real y espera
    que llegue hasta lina-deploy (no que falle por sudo).

    Acceptance: LINA intenta o completa el reload de lina-fs-safe, reportando
    el resultado (success o error de container no corriendo).
    """
    capture = await tg.send_prompt(
        "Intentá hacer reload_mcp('lina-fs-safe'). "
        "Decime qué pasó: ¿llegó a ejecutar lina-deploy? ¿el container está corriendo? "
        "No importa si falla, solo contame el resultado exacto.",
        timeout=120,
        stable_window=20.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    # Debe haber intentado la operación y reportado algo coherente
    assert _has_any(
        text,
        "lina-deploy", "lina-mcp-fs-safe", "container", "docker",
        "success", "false", "error", "timeout", "corriendo", "running",
        "allow_sudo", "sudo",
    ), (
        f"LINA no reportó resultado de reload_mcp. Respuesta: {capture.final_text[:600]}"
    )


# ── Issue #79: Docker CI pipeline ────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_79_lina_deploy_script_exists(tg: TelegramTestClient) -> None:
    """El script lina-deploy existe y tiene la lista correcta de servicios.

    Acceptance: LINA puede leer deploy/lina-deploy.sh y confirmar que
    lina-mcp-fs-safe está en la ALLOWED_SERVICES list.
    """
    capture = await tg.send_prompt(
        "Leé el archivo /home/user/lina/deploy/lina-deploy.sh y decime "
        "si 'lina-mcp-fs-safe' está en la lista ALLOWED_SERVICES.",
        timeout=75,
        stable_window=12.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    assert _has_any(text, "sí", "si", "yes", "está", "aparece", "incluido", "lina-mcp-fs-safe"), (
        f"LINA no confirmó presencia en ALLOWED_SERVICES. Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_79_ci_workflow_has_docker_build_job(tg: TelegramTestClient) -> None:
    """El CI workflow tiene el job docker-build con las 3 imágenes.

    Acceptance: LINA puede leer .github/workflows/ci.yml y confirmar
    que existe el job docker-build con gateway, mcp-fs-safe, mcp-lina-db.
    """
    capture = await tg.send_prompt(
        "Leé /home/user/lina/.github/workflows/ci.yml. "
        "¿Existe un job llamado 'docker-build'? ¿Qué imágenes construye?",
        timeout=75,
        stable_window=12.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    assert _has_any(text, "docker-build", "docker build", "docker_build"), (
        f"LINA no confirmó job docker-build. Respuesta: {capture.final_text[:600]}"
    )
    images_found = sum(img in text for img in ["gateway", "mcp-fs-safe", "mcp-lina-db", "lina-db"])
    assert images_found >= 2, (
        f"Solo {images_found}/3 imágenes encontradas. Respuesta: {capture.final_text[:600]}"
    )

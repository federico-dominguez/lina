"""E2E Telegram — Runbook reload_mcp (issue #98).

Verifica que LINA puede ejecutar el flujo documentado en
docs/runbooks/0005-reload-mcp-safely.md de extremo a extremo:

1. Pedir un reload de lina-fs-safe.
2. Después del reload, listar /home/user/lina vía fs-safe (debe funcionar).

Ejecución:
    pytest -m e2e_telegram tests/e2e/telegram/scenarios/test_reload_mcp_runbook.py -v
"""

from __future__ import annotations

import pytest

from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_reload_fs_safe_then_list(tg: TelegramTestClient) -> None:
    """LINA puede reiniciar fs-safe y seguir usándolo en la misma sesión.

    Acceptance:
        - Responde mencionando el reload con `success` o `reiniciado`/`recargado`.
        - Después del reload, lista algún archivo/dir conocido del repo.
    """
    capture = await tg.send_prompt(
        "Paso 1: ejecutá reload_mcp('lina-fs-safe') a través de sh_run. "
        "Paso 2: una vez confirmado el reload, listame el contenido del "
        "directorio /home/user/lina con la tool de fs-safe. "
        "Reportá ambos pasos.",
        timeout=120,
        stable_window=20.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    reload_ok = any(
        kw in text
        for kw in ["success", "reiniciad", "recargad", "reload", "fs-safe", "duration"]
    )
    assert reload_ok, (
        f"LINA no reportó el reload de fs-safe. Respuesta: {capture.final_text[:600]}"
    )

    listing_ok = any(
        kw in text for kw in ["readme", "agents", "pyproject", "mcps", "docs", "config"]
    )
    assert listing_ok, (
        f"LINA no listó el contenido del repo post-reload. "
        f"Respuesta: {capture.final_text[:600]}"
    )


@pytest.mark.e2e_telegram
async def test_runbook_lists_reloadable_mcps(tg: TelegramTestClient) -> None:
    """LINA puede consultar el runbook y enumerar MCPs reloadables.

    Acceptance: respuesta menciona ≥4 de los 9 MCPs documentados.
    """
    capture = await tg.send_prompt(
        "Leé el runbook docs/runbooks/0005-reload-mcp-safely.md y decime "
        "qué MCPs son reloadables. Lístalos.",
        timeout=90,
        stable_window=15.0,
    )
    assert capture.final_messages, "LINA no respondió"
    text = capture.final_text.lower()

    known = [
        "lina-secrets",
        "lina-fs-safe",
        "lina-shell-policy",
        "lina-systemd-user",
        "lina-moodle",
        "lina-db",
        "lina-gitlab",
        "lina-github",
        "lina-gcalendar",
    ]
    found = sum(name in text for name in known)
    assert found >= 4, (
        f"Solo {found}/9 MCPs reloadables mencionados. "
        f"Respuesta: {capture.final_text[:600]}"
    )

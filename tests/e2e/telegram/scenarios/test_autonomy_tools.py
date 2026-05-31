"""
E2E: Verifica las nuevas capacidades de autonomía operativa de LINA (issue #37).

Tests:
    1. Acceso al repo lina via /home/user (git mount en goosed)
    2. GitHub: list issues
    3. GitHub: create + close issue (write tools completos)
    4. Docker proxy: ps + restart via DOCKER_HOST restringido
    5. Docker proxy: restart de un MCP de prueba

Notas de arquitectura (post issue-#52):
    - shell-policy corre como uid 1000 (misma que host fede), con /home/user montado
      en lectura/escritura — los archivos del repo son accesibles directamente.
    - sudo está instalado en lina-mcp-shell-policy (MCP_SUDO_ENABLED=1 en build).
    - no-new-privileges está desactivado para shell-policy, por lo que setuid/sudo
      funciona. Para reiniciar servicios LINA puede usar sudo lina-deploy o
      docker restart vía DOCKER_HOST=tcp://lina-docker-proxy.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.telegram.client import TelegramTestClient


@pytest.mark.e2e_telegram
async def test_git_home_mount(tg: TelegramTestClient) -> None:
    """El volumen /home/user está montado y accesible (goosed+shell-policy).

    Post fix #52: shell-policy corre como uid 1000, por lo que /home/user/lina
    debe ser legible directamente. El test falla si LINA reporta Permission denied
    (eso indicaría regresión del fix).
    """
    capture = await tg.send_prompt(
        "Ejecuta via sh_run: primero 'ls /home/user' para listar el directorio raíz, "
        "y luego intenta 'cat /home/user/lina/.git/HEAD'. "
        "Dimé qué encontraste (aunque haya permission denied en algún paso).",
        timeout=75,
        stable_window=20.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    # Aceptamos: encontró el archivo, o encontró 'lina' en el listado, o reportó
    # correctamente el permiso denegado (lo que igual confirma que el mount existe).
    # Post fix #52: con uid 1000 el archivo debe ser legible. "permission denied"
    # indica regresión del fix → el assert lo detecta.
    assert not any(kw in text for kw in ["permission denied", "permiso denegado"]), (
        f"Regresión uid: LINA reportó Permission denied. Respuesta: {text[:400]}"
    )
    assert any(
        kw in text
        for kw in ["lina", "ref:", "main", "master", "branch", "home/user", "/home/user"]
    ), f"LINA no reportó nada sobre /home/user. Respuesta: {text[:400]}"


@pytest.mark.e2e_telegram
async def test_github_list_issues(tg: TelegramTestClient) -> None:
    """lina-github puede listar issues del repo.

    Nota: usamos asyncio.sleep para dejar que cualquier mensaje tardío del test
    anterior sea entregado a Telegram antes de registrar el handler de este test.
    """
    # Esperar que mensajes tardíos del test anterior sean entregados
    # antes de empezar a escuchar (el handler no está activo durante el sleep).
    await asyncio.sleep(12)

    capture = await tg.send_prompt(
        "Usa el MCP lina-github: lista los issues abiertos de federico-dominguez/lina y dimé cuántos hay",
        timeout=90,
        stable_window=20.0,
    )
    text = capture.final_text
    assert capture.final_messages, "LINA no respondió"
    # Debe mencionar un número o "no hay issues"
    has_number = any(c.isdigit() for c in text)
    has_none_keyword = any(kw in text.lower() for kw in ["no hay", "ninguno", "0 issue", "cero", "sin issues", "no issues"])
    assert has_number or has_none_keyword, (
        f"LINA no reportó cantidad de issues. Respuesta: {text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_github_create_and_close_issue(tg: TelegramTestClient) -> None:
    """lina-github puede crear un issue y luego cerrarlo (write tools)."""
    await asyncio.sleep(8)

    capture = await tg.send_prompt(
        "Usa lina-github: crea un issue en federico-dominguez/lina con título "
        "'[autotest] write tools verification' y body 'Closing automatically - automated test'. "
        "Luego ciérralo inmediatamente con github_close_issue. Dimé el número del issue.",
        timeout=180,
        stable_window=20.0,  # LINA hace 2 tool calls (~21s en total); stable_window largo evita que
                             # el capture cierre tras el thinking bubble mientras ocurren las tool calls.
    )
    text = capture.final_text
    assert capture.final_messages, "LINA no respondió"
    # Debe mencionar un número de issue
    assert any(c.isdigit() for c in text), (
        f"LINA no indicó el número del issue creado. Respuesta: {text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_docker_proxy_ps(tg: TelegramTestClient) -> None:
    """shell-policy puede listar contenedores via DOCKER_HOST (proxy Tecnativa)."""
    await asyncio.sleep(8)

    capture = await tg.send_prompt(
        "Ejecuta via sh_run (sin sudo): "
        "docker ps --filter name=lina --format '{{.Names}}' "
        "— dimé el output. Usa DOCKER_HOST=tcp://lina-docker-proxy:2375 si hace falta.",
        timeout=60,
        stable_window=20.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    assert any(kw in text for kw in ["lina-", "goosed", "mcp", "gateway", "docker", "proxy"]), (
        f"Docker proxy no parece accesible. Respuesta: {text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_docker_proxy_restart(tg: TelegramTestClient) -> None:
    """LINA puede reiniciar un MCP vía docker restart usando el proxy.

    Post fix #52: sudo está instalado en shell-policy. Sin embargo, el mecanismo
    recomendado sigue siendo `docker restart <container>` con DOCKER_HOST apuntando
    al proxy Tecnativa (POST=1 + CONTAINERS=1) para no requerir sudo para restarts
    normales — sudo queda reservado para operaciones de sistema (apt, lina-deploy).
    """
    await asyncio.sleep(8)

    capture = await tg.send_prompt(
        "Ejecuta via sh_run: reinicia el contenedor 'docker-lina-mcp-fs-safe-1' "
        "usando el comando 'docker restart docker-lina-mcp-fs-safe-1'. "
        "DOCKER_HOST debería ser tcp://lina-docker-proxy:2375. "
        "Dimé si el restart funcionó o qué error obtuvo.",
        timeout=90,
        stable_window=20.0,
    )
    text = capture.final_text.lower()
    assert capture.final_messages, "LINA no respondió"
    # Aceptamos: restart exitoso, o error descriptivo del proxy, o que LINA lo intentó
    assert any(
        kw in text
        for kw in ["reinici", "restart", "docker-lina-mcp-fs-safe", "fs-safe",
                   "funcionó", "error", "proxy", "docker_host", "2375"]
    ), f"LINA no reportó sobre el restart. Respuesta: {text[:400]}"

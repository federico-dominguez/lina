"""E2E integration tests — initialize handshake y tools/call para todos los MCPs stdio.

Verifica que cada MCP puede iniciarse, completar el handshake MCP,
responder a tools/list y ejecutar una herramienta representativa.
No requiere credenciales reales (moodle/tools/call se omite por requerir token).
"""

from __future__ import annotations

import os
import pytest

from tests.integration.mcps.conftest import McpStdioClient


REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# (mcp_dir, cmd, expected_name)
MCP_PARAMS = [
    ("mcps/secrets", "lina-secrets", "lina-secrets"),
    ("mcps/fs-safe", "lina-fs-safe", "lina-fs-safe"),
    ("mcps/shell-policy", "lina-shell-policy", "lina-shell-policy"),
    ("mcps/systemd-user", "lina-systemd-user", "lina-systemd-user"),
    ("mcps/moodle", "lina-moodle", "lina-moodle"),
    ("mcps/lina-db", "lina-db", "lina-db"),
]

# (mcp_dir, cmd, tool_name, tool_args)
# tool_args = None → se reemplaza por {"path": str(tmp_path)} en el test
MCP_TOOLS_CALL_PARAMS = [
    ("mcps/secrets", "lina-secrets", "keyring_backend", {}),
    ("mcps/fs-safe", "lina-fs-safe", "fs_list", None),  # args dependen de tmp_path
    ("mcps/shell-policy", "lina-shell-policy", "sh_explain", {"command": "ls -la"}),
    ("mcps/systemd-user", "lina-systemd-user", "svc_list_lina", {}),
    pytest.param(
        "mcps/moodle", "lina-moodle", "moodle_get_courses", {},
        marks=pytest.mark.skip(reason="requiere MOODLE_TOKEN válido — ver ADR-0008"),
    ),
    pytest.param(
        "mcps/lina-db", "lina-db", "get_memory", {"key": "test"},
        marks=pytest.mark.skip(reason="requiere PostgreSQL activo — ver Issue #9"),
    ),
]

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mcp_dir,cmd,expected_name", MCP_PARAMS)
def test_mcp_initialize(mcp_dir, cmd, expected_name, tmp_path):
    """Cada MCP debe completar el handshake MCP y devolver su nombre correcto."""
    cwd = os.path.join(REPO_ROOT, mcp_dir)
    env = {
        "MCP_TRANSPORT": "stdio",
        "LINA_FS_ALLOWLIST": str(tmp_path),  # solo usado por fs-safe
    }
    with McpStdioClient(cmd, cwd=cwd, env=env) as client:
        result = client.initialize()
    assert result is not None, f"{cmd}: initialize() devolvió None"
    assert "serverInfo" in result, f"{cmd}: falta serverInfo en respuesta"
    assert result["serverInfo"]["name"] == expected_name, (
        f"{cmd}: nombre esperado={expected_name!r}, got={result['serverInfo']['name']!r}"
    )


@pytest.mark.parametrize("mcp_dir,cmd,expected_name", MCP_PARAMS)
def test_mcp_tools_list(mcp_dir, cmd, expected_name, tmp_path):
    """Cada MCP debe devolver al menos una herramienta en tools/list."""
    cwd = os.path.join(REPO_ROOT, mcp_dir)
    env = {
        "MCP_TRANSPORT": "stdio",
        "LINA_FS_ALLOWLIST": str(tmp_path),
    }
    with McpStdioClient(cmd, cwd=cwd, env=env) as client:
        client.initialize()
        tools = client.tools_list()
    assert len(tools) >= 1, f"{cmd}: tools/list devolvió lista vacía"
    tool_names = [t["name"] for t in tools]
    assert all(isinstance(n, str) and n for n in tool_names)


@pytest.mark.parametrize("mcp_dir,cmd,tool_name,tool_args", MCP_TOOLS_CALL_PARAMS)
def test_mcp_tools_call(mcp_dir, cmd, tool_name, tool_args, tmp_path):
    """Cada MCP debe poder ejecutar una herramienta representativa sin credenciales."""
    cwd = os.path.join(REPO_ROOT, mcp_dir)
    args = tool_args if tool_args is not None else {"path": str(tmp_path)}
    env = {
        "MCP_TRANSPORT": "stdio",
        "LINA_FS_ALLOWLIST": str(tmp_path),
    }
    with McpStdioClient(cmd, cwd=cwd, env=env) as client:
        client.initialize()
        result = client.tools_call(tool_name, args)
    # MCP tools/call response: {"content": [...], "isError": bool}
    assert result is not None, f"{cmd}/{tool_name}: tools/call devolvió None"
    assert not result.get("isError", False), (
        f"{cmd}/{tool_name}: tools/call devolvió isError=True: {result}"
    )
    assert "content" in result, f"{cmd}/{tool_name}: respuesta sin campo 'content'"

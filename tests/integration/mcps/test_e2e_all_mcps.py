"""E2E integration tests — initialize handshake para todos los MCPs stdio.

Verifica que cada MCP puede iniciarse, completar el handshake MCP
y responder a tools/list. No requiere credenciales reales.
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

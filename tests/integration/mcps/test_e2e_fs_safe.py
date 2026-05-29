"""E2E integration tests — lina-fs-safe over stdio.

Estos tests arrancan el proceso real del MCP y verifican el protocolo JSON-RPC.
Requieren que el venv de lina-fs-safe esté sincronizado (`uv sync`).
"""

from __future__ import annotations

import os
import pytest

from tests.integration.mcps.conftest import McpStdioClient


MCP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "mcps", "fs-safe"))

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fs_e2e")
    with McpStdioClient(
        "lina-fs-safe",
        cwd=MCP_DIR,
        env={"MCP_TRANSPORT": "stdio", "LINA_FS_ALLOWLIST": str(tmp)},
    ) as c:
        yield c


def test_initialize(client):
    result = client.initialize()
    assert result is not None
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "lina-fs-safe"


def test_tools_list(client):
    tools = client.tools_list()
    names = [t["name"] for t in tools]
    assert "fs_read" in names
    assert "fs_write" in names
    assert "fs_list" in names
    assert "fs_stat" in names


def test_tools_call_fs_stat(client, tmp_path):
    result = client.tools_call("fs_stat", {"path": str(tmp_path)})
    assert result is not None

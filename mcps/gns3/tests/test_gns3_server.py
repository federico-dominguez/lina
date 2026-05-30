"""Unit tests — lina-gns3.

Verifica config, helpers HTTP y tools usando respx.
"""
from __future__ import annotations

import importlib

import httpx
import pytest
import respx


@pytest.fixture(autouse=True)
def reload_module(monkeypatch):
    monkeypatch.setenv("GNS3_HOST", "localhost")
    monkeypatch.setenv("GNS3_PORT", "3080")
    monkeypatch.setenv("GNS3_USER", "")
    monkeypatch.setenv("GNS3_PASSWORD", "")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "8000")
    import lina_gns3.server as m
    importlib.reload(m)
    return m


class TestClientConfig:
    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "192.168.1.10")
        monkeypatch.setenv("GNS3_PORT", "3080")
        import lina_gns3.server as m
        importlib.reload(m)
        c = m._client()
        assert "192.168.1.10" in str(c.base_url)

    def test_auth_set_when_user_provided(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        monkeypatch.setenv("GNS3_USER", "admin")
        monkeypatch.setenv("GNS3_PASSWORD", "secret")
        import lina_gns3.server as m
        importlib.reload(m)
        c = m._client()
        assert c.auth is not None

    def test_no_auth_when_user_empty(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        monkeypatch.setenv("GNS3_USER", "")
        monkeypatch.setenv("GNS3_PASSWORD", "")
        import lina_gns3.server as m
        importlib.reload(m)
        c = m._client()
        assert c.auth is None


class TestListProjects:
    @respx.mock
    def test_returns_projects(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects").mock(
            return_value=httpx.Response(200, json=[{"project_id": "abc", "name": "Lab1", "status": "closed"}])
        )
        result = m.gns3_list_projects()
        assert result[0]["name"] == "Lab1"

    @respx.mock
    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects").mock(
            return_value=httpx.Response(500, json={"message": "Internal Server Error"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m.gns3_list_projects()


class TestNodeActions:
    @respx.mock
    def test_start_node_posts(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        route = respx.post("http://localhost:3080/v2/projects/p1/nodes/n1/start").mock(
            return_value=httpx.Response(200, json={"node_id": "n1", "status": "started"})
        )
        result = m.gns3_start_node("p1", "n1")
        assert result["status"] == "started"
        assert route.called

    @respx.mock
    def test_stop_node_posts(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        respx.post("http://localhost:3080/v2/projects/p1/nodes/n1/stop").mock(
            return_value=httpx.Response(200, json={"node_id": "n1", "status": "stopped"})
        )
        result = m.gns3_stop_node("p1", "n1")
        assert result["status"] == "stopped"


class TestGetNodeConsole:
    @respx.mock
    def test_returns_console_info(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/p1/nodes/n1").mock(
            return_value=httpx.Response(200, json={
                "node_id": "n1",
                "name": "R1",
                "console": 5000,
                "console_type": "telnet",
            })
        )
        result = m.gns3_get_node_console("p1", "n1")
        assert result["console"] == 5000
        assert result["console_type"] == "telnet"
        assert result["name"] == "R1"


class TestVersion:
    @respx.mock
    def test_version_endpoint(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m
        importlib.reload(m)
        respx.get("http://localhost:3080/v2/version").mock(
            return_value=httpx.Response(200, json={"version": "2.2.50"})
        )
        result = m.gns3_version()
        assert result["version"] == "2.2.50"


class TestEnvConfig:
    def test_transport_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        import lina_gns3.server as m
        importlib.reload(m)
        assert m._MCP_TRANSPORT == "streamable-http"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_PORT", "9090")
        import lina_gns3.server as m
        importlib.reload(m)
        assert m._MCP_HTTP_PORT == 9090

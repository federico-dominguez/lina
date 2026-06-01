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
            return_value=httpx.Response(
                200, json=[{"project_id": "abc", "name": "Lab1", "status": "closed"}]
            )
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
            return_value=httpx.Response(
                200,
                json={
                    "node_id": "n1",
                    "name": "R1",
                    "console": 5000,
                    "console_type": "telnet",
                },
            )
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


class TestProjectCRUD:
    @respx.mock
    def test_get_project(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/abc").mock(
            return_value=httpx.Response(
                200, json={"project_id": "abc", "name": "Lab1", "status": "closed"}
            )
        )
        result = m.gns3_get_project("abc")
        assert result["project_id"] == "abc"

    @respx.mock
    def test_open_project(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.post("http://localhost:3080/v2/projects/abc/open").mock(
            return_value=httpx.Response(200, json={"project_id": "abc", "status": "opened"})
        )
        result = m.gns3_open_project("abc")
        assert result["status"] == "opened"

    @respx.mock
    def test_close_project(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.post("http://localhost:3080/v2/projects/abc/close").mock(
            return_value=httpx.Response(200, json={"project_id": "abc", "status": "closed"})
        )
        result = m.gns3_close_project("abc")
        assert result["status"] == "closed"

    @respx.mock
    def test_get_project_not_found(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/notexist").mock(
            return_value=httpx.Response(404, json={"message": "Project not found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m.gns3_get_project("notexist")


class TestNodeList:
    @respx.mock
    def test_list_nodes(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/p1/nodes").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"node_id": "n1", "name": "R1", "node_type": "dynamips"},
                    {"node_id": "n2", "name": "SW1", "node_type": "ethernet_switch"},
                ],
            )
        )
        result = m.gns3_list_nodes("p1")
        assert len(result) == 2
        assert result[0]["name"] == "R1"

    @respx.mock
    def test_get_node(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/p1/nodes/n1").mock(
            return_value=httpx.Response(
                200, json={"node_id": "n1", "name": "R1", "status": "started"}
            )
        )
        result = m.gns3_get_node("p1", "n1")
        assert result["name"] == "R1"


class TestBulkNodeControl:
    @respx.mock
    def test_start_all_nodes(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        route = respx.post("http://localhost:3080/v2/projects/p1/nodes/start").mock(
            return_value=httpx.Response(204, json={})
        )
        m.gns3_start_all_nodes("p1")
        assert route.called

    @respx.mock
    def test_stop_all_nodes(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        route = respx.post("http://localhost:3080/v2/projects/p1/nodes/stop").mock(
            return_value=httpx.Response(204, json={})
        )
        m.gns3_stop_all_nodes("p1")
        assert route.called


class TestLinks:
    @respx.mock
    def test_list_links(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/p1/links").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"link_id": "l1", "link_type": "ethernet"},
                ],
            )
        )
        result = m.gns3_list_links("p1")
        assert len(result) == 1
        assert result[0]["link_id"] == "l1"

    @respx.mock
    def test_list_links_empty(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.get("http://localhost:3080/v2/projects/p1/links").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = m.gns3_list_links("p1")
        assert result == []


class TestPutDelete:
    @respx.mock
    def test_put_updates_resource(self, monkeypatch):
        """_put helper sends PUT and returns JSON."""
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.put("http://localhost:3080/v2/projects/p1").mock(
            return_value=httpx.Response(200, json={"project_id": "p1", "name": "Updated"})
        )
        result = m._put("/projects/p1", {"name": "Updated"})
        assert result["name"] == "Updated"

    @respx.mock
    def test_delete_returns_status_code(self, monkeypatch):
        """_delete helper sends DELETE and returns status code."""
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.delete("http://localhost:3080/v2/projects/p1").mock(return_value=httpx.Response(204))
        status = m._delete("/projects/p1")
        assert status == 204

    @respx.mock
    def test_put_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.put("http://localhost:3080/v2/projects/p1").mock(
            return_value=httpx.Response(422, json={"message": "Validation error"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m._put("/projects/p1", {})

    @respx.mock
    def test_delete_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("GNS3_HOST", "localhost")
        import lina_gns3.server as m

        importlib.reload(m)
        respx.delete("http://localhost:3080/v2/projects/notexist").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m._delete("/projects/notexist")

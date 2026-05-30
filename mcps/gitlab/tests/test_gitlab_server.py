"""Unit tests — lina-gitlab.

Verifica helpers de config, headers y tools usando respx.
"""
from __future__ import annotations

import importlib

import httpx
import pytest
import respx


@pytest.fixture(autouse=True)
def reload_module(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat_test")
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "8000")
    import lina_gitlab.server as m
    importlib.reload(m)
    return m


class TestClientHeaders:
    def test_auth_header_set(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "glpat_abc")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        c = m._client()
        assert c.headers.get("Private-Token") == "glpat_abc"

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://mygitlab.io")
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        import lina_gitlab.server as m
        importlib.reload(m)
        c = m._client()
        assert "mygitlab.io" in str(c.base_url)

    def test_no_token_raises(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "")
        monkeypatch.setenv("GITLAB_TOKEN_KEYRING", "")
        import lina_gitlab.server as m
        importlib.reload(m)
        with pytest.raises(RuntimeError, match="No hay token"):
            m._load_token()


class TestEncodeId:
    def test_numeric_id_unchanged(self, monkeypatch):
        import lina_gitlab.server as m
        importlib.reload(m)
        assert m._encode_id("5") == "5"

    def test_namespace_path_encoded(self, monkeypatch):
        import lina_gitlab.server as m
        importlib.reload(m)
        assert m._encode_id("my-group/my-project") == "my-group%2Fmy-project"


class TestListProjects:
    @respx.mock
    def test_returns_list(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.get("https://gitlab.example.com/api/v4/projects").mock(
            return_value=httpx.Response(200, json=[{
                "id": 1, "name": "proj-a",
                "path_with_namespace": "ns/proj-a",
                "web_url": "https://gitlab.example.com/ns/proj-a",
            }])
        )
        result = m.gitlab_list_projects()
        assert result[0]["name"] == "proj-a"

    @respx.mock
    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.get("https://gitlab.example.com/api/v4/projects").mock(
            return_value=httpx.Response(401, json={"message": "Unauthorized"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m.gitlab_list_projects()


class TestGetProject:
    @respx.mock
    def test_returns_project_detail(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.get("https://gitlab.example.com/api/v4/projects/42").mock(
            return_value=httpx.Response(200, json={
                "id": 42, "name": "myproj",
                "path_with_namespace": "ns/myproj",
                "web_url": "https://gitlab.example.com/ns/myproj",
                "default_branch": "main",
            })
        )
        result = m.gitlab_get_project("42")
        assert result["id"] == 42
        assert result["default_branch"] == "main"


class TestListBranches:
    @respx.mock
    def test_returns_branches(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.get("https://gitlab.example.com/api/v4/projects/1/repository/branches").mock(
            return_value=httpx.Response(200, json=[
                {"name": "main", "default": True, "protected": True,
                 "commit": {"id": "abc", "committed_date": "2025-01-01T00:00:00Z",
                            "message": "Initial commit"}},
            ])
        )
        result = m.gitlab_list_branches("1")
        assert result[0]["name"] == "main"


class TestListIssues:
    @respx.mock
    def test_returns_issues(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.get("https://gitlab.example.com/api/v4/projects/1/issues").mock(
            return_value=httpx.Response(200, json=[{
                "iid": 1, "title": "Bug", "state": "opened",
                "labels": [], "web_url": "https://gitlab.example.com/-/issues/1"
            }])
        )
        result = m.gitlab_list_issues("1")
        assert result[0]["title"] == "Bug"


class TestCreateIssue:
    @respx.mock
    def test_posts_payload(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.post("https://gitlab.example.com/api/v4/projects/5/issues").mock(
            return_value=httpx.Response(201, json={
                "iid": 3, "title": "Bug", "state": "opened",
                "web_url": "https://gitlab.example.com/ns/proj/-/issues/3"
            })
        )
        result = m.gitlab_create_issue("5", "Bug", description="desc")
        assert result["iid"] == 3
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["title"] == "Bug"


class TestTransportConfig:
    def test_transport_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        assert m._MCP_TRANSPORT == "streamable-http"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_PORT", "9999")
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        assert m._MCP_HTTP_PORT == 9999


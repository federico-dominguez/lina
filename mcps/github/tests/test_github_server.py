"""Unit tests — lina-github.

Verifica helpers de configuración, construcción de headers y herramientas
usando respx para interceptar llamadas httpx sin tráfico real a GitHub.
"""
from __future__ import annotations

import importlib
import os

import httpx
import pytest
import respx


# ── helpers de módulo ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reload_module(monkeypatch):
    """Recarga el módulo server para que tome las env vars del test."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "8000")
    import lina_github.server as m
    importlib.reload(m)
    return m


def server():
    import lina_github.server as m
    return m


# ── _client() incluye Authorization header ────────────────────────────────────

class TestClientHeaders:
    def test_auth_header_present_with_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        import lina_github.server as m
        importlib.reload(m)
        c = m._client()
        assert "Authorization" in c.headers
        assert c.headers["Authorization"] == "Bearer ghp_abc123"

    def test_no_auth_header_without_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "")
        import lina_github.server as m
        importlib.reload(m)
        c = m._client()
        assert "Authorization" not in c.headers

    def test_accept_header_set(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        c = m._client()
        assert c.headers["Accept"] == "application/vnd.github+json"


# ── github_list_repos ─────────────────────────────────────────────────────────

class TestListRepos:
    @respx.mock
    def test_returns_list(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        respx.get("https://api.github.com/users/myorg/repos").mock(
            return_value=httpx.Response(200, json=[{"name": "repo-a"}, {"name": "repo-b"}])
        )
        result = m.github_list_repos("myorg")
        assert len(result) == 2
        assert result[0]["name"] == "repo-a"

    @respx.mock
    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        respx.get("https://api.github.com/users/ghost/repos").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            m.github_list_repos("ghost")


# ── github_get_file decodes base64 ────────────────────────────────────────────

class TestGetFile:
    @respx.mock
    def test_decodes_base64_content(self, monkeypatch):
        import base64
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        encoded = base64.b64encode(b"hello world").decode() + "\n"
        respx.get("https://api.github.com/repos/o/r/contents/README.md").mock(
            return_value=httpx.Response(200, json={"name": "README.md", "content": encoded, "type": "file"})
        )
        result = m.github_get_file("o", "r", "README.md")
        assert result["content"] == "hello world"

    @respx.mock
    def test_directory_returns_entries(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        respx.get("https://api.github.com/repos/o/r/contents/src").mock(
            return_value=httpx.Response(200, json=[
                {"name": "main.py", "type": "file"},
                {"name": "utils", "type": "dir"},
            ])
        )
        result = m.github_get_file("o", "r", "src")
        assert result["type"] == "directory"
        assert len(result["entries"]) == 2


# ── github_create_issue ───────────────────────────────────────────────────────

class TestCreateIssue:
    @respx.mock
    def test_posts_correct_payload(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        route = respx.post("https://api.github.com/repos/o/r/issues").mock(
            return_value=httpx.Response(201, json={"number": 42, "title": "Bug"})
        )
        result = m.github_create_issue("o", "r", "Bug", body="details", labels=["bug"])
        assert result["number"] == 42
        sent = route.calls[0].request
        import json
        body = json.loads(sent.content)
        assert body["title"] == "Bug"
        assert body["labels"] == ["bug"]


# ── github_list_workflow_runs ─────────────────────────────────────────────────

class TestWorkflowRuns:
    @respx.mock
    def test_all_runs_path(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        respx.get("https://api.github.com/repos/o/r/actions/runs").mock(
            return_value=httpx.Response(200, json={"workflow_runs": [{"id": 1}]})
        )
        result = m.github_list_workflow_runs("o", "r")
        assert result[0]["id"] == 1

    @respx.mock
    def test_specific_workflow_path(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        import lina_github.server as m
        importlib.reload(m)
        respx.get("https://api.github.com/repos/o/r/actions/workflows/ci.yml/runs").mock(
            return_value=httpx.Response(200, json={"workflow_runs": [{"id": 99}]})
        )
        result = m.github_list_workflow_runs("o", "r", workflow_id="ci.yml")
        assert result[0]["id"] == 99


# ── transport / port env vars ─────────────────────────────────────────────────

class TestEnvConfig:
    def test_transport_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        import lina_github.server as m
        importlib.reload(m)
        assert m._MCP_TRANSPORT == "streamable-http"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_PORT", "9090")
        import lina_github.server as m
        importlib.reload(m)
        assert m._MCP_HTTP_PORT == 9090

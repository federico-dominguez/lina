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


# ── Write tools ────────────────────────────────────────────────────────────────

class TestCreateBranch:
    @respx.mock
    def test_posts_branch_ref(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.post("https://gitlab.example.com/api/v4/projects/5/repository/branches").mock(
            return_value=httpx.Response(201, json={
                "name": "feat/x",
                "commit": {"id": "abc123def456"},
                "web_url": "https://gitlab.example.com/-/tree/feat/x",
            })
        )
        result = m.gitlab_create_branch("5", "feat/x", "main")
        assert result["name"] == "feat/x"
        body = _json.loads(route.calls[0].request.content)
        assert body["branch"] == "feat/x"
        assert body["ref"] == "main"


class TestDeleteBranch:
    @respx.mock
    def test_calls_delete(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        respx.delete("https://gitlab.example.com/api/v4/projects/5/repository/branches/feat%2Fx").mock(
            return_value=httpx.Response(204)
        )
        result = m.gitlab_delete_branch("5", "feat/x")
        assert result == {}


class TestCreateOrUpdateFile:
    @respx.mock
    def test_encodes_base64(self, monkeypatch):
        import base64, json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        # PUT succeeds (update path)
        route = respx.put("https://gitlab.example.com/api/v4/projects/5/repository/files/README.md").mock(
            return_value=httpx.Response(200, json={"file_path": "README.md", "branch": "main"})
        )
        result = m.gitlab_create_or_update_file("5", "README.md", "hello", "init", "main")
        assert result["file_path"] == "README.md"
        body = _json.loads(route.calls[0].request.content)
        decoded = base64.b64decode(body["content"]).decode()
        assert decoded == "hello"

    @respx.mock
    def test_falls_back_to_post_on_error(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        # PUT fails (new file), POST succeeds
        respx.put("https://gitlab.example.com/api/v4/projects/5/repository/files/new.py").mock(
            return_value=httpx.Response(400, json={"message": "does not exist"})
        )
        respx.post("https://gitlab.example.com/api/v4/projects/5/repository/files/new.py").mock(
            return_value=httpx.Response(201, json={"file_path": "new.py", "branch": "main"})
        )
        result = m.gitlab_create_or_update_file("5", "new.py", "print('hi')", "add", "main")
        assert result["file_path"] == "new.py"


class TestAddComment:
    @respx.mock
    def test_posts_note_to_issue(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.post("https://gitlab.example.com/api/v4/projects/5/issues/3/notes").mock(
            return_value=httpx.Response(201, json={
                "id": 77, "author": {"username": "fede"}, "created_at": "2024-01-01T00:00:00Z"
            })
        )
        result = m.gitlab_add_comment("5", "issues", 3, "Looks good!")
        assert result["id"] == 77
        body = _json.loads(route.calls[0].request.content)
        assert body["body"] == "Looks good!"


class TestCloseIssue:
    @respx.mock
    def test_puts_state_close(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.put("https://gitlab.example.com/api/v4/projects/5/issues/3").mock(
            return_value=httpx.Response(200, json={
                "iid": 3, "title": "Bug", "state": "closed",
                "web_url": "https://gitlab.example.com/-/issues/3"
            })
        )
        result = m.gitlab_close_issue("5", 3)
        assert result["state"] == "closed"
        body = _json.loads(route.calls[0].request.content)
        assert body["state_event"] == "close"

    @respx.mock
    def test_adds_note_before_closing(self, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        note_route = respx.post("https://gitlab.example.com/api/v4/projects/5/issues/3/notes").mock(
            return_value=httpx.Response(201, json={"id": 1, "author": {"username": "bot"}})
        )
        respx.put("https://gitlab.example.com/api/v4/projects/5/issues/3").mock(
            return_value=httpx.Response(200, json={
                "iid": 3, "title": "Bug", "state": "closed",
                "web_url": "https://gitlab.example.com/-/issues/3"
            })
        )
        m.gitlab_close_issue("5", 3, comment="Fixed!")
        assert note_route.called


class TestMergeMr:
    @respx.mock
    def test_puts_merge(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.put("https://gitlab.example.com/api/v4/projects/5/merge_requests/2/merge").mock(
            return_value=httpx.Response(200, json={
                "iid": 2, "title": "feat: x", "state": "merged",
                "merged_at": "2024-01-01T00:00:00Z",
                "web_url": "https://gitlab.example.com/-/merge_requests/2"
            })
        )
        result = m.gitlab_merge_mr("5", 2)
        assert result["state"] == "merged"
        body = _json.loads(route.calls[0].request.content)
        assert body["squash"] is False

    @respx.mock
    def test_squash_flag_forwarded(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        import lina_gitlab.server as m
        importlib.reload(m)
        route = respx.put("https://gitlab.example.com/api/v4/projects/5/merge_requests/2/merge").mock(
            return_value=httpx.Response(200, json={
                "iid": 2, "title": "feat: x", "state": "merged",
                "merged_at": "2024-01-01T00:00:00Z",
                "web_url": "https://gitlab.example.com/-/merge_requests/2"
            })
        )
        m.gitlab_merge_mr("5", 2, squash=True)
        body = _json.loads(route.calls[0].request.content)
        assert body["squash"] is True


"""Tests para SpawnerService y server tools (issue #84, #85).

El nuevo SpawnerService usa HTTP API de goosed (requests.post) en vez de
subprocess.Popen. No hay config YAML, no hay tracking de procesos nativos,
no hay seniales SIGTERM/SIGKILL — todo se maneja via goosed HTTP API + DB.

Estrategia:
  - SpawnerService usa requests.post y psycopg2; ambos se mockean.
  - Las tools de server.py se verifican con spawner mockeado.
  - Coverage target: 80% (lineas de SSE streaming excluidas).
"""

from __future__ import annotations

import importlib
import textwrap
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import pytest

FULL_YAML = textwrap.dedent("""\
    version: "1"
    roles:
      dev:
        description: Dev role
        allowed_mcps: [lina-fs-safe, lina-github]
        sudo_allowed: false
        max_runtime_minutes: 30
        max_tokens_per_run: 50000
        max_usd_per_day: 2.0
        needs_approval_for: [pr_merge]
      research:
        description: Research role
        allowed_mcps: [lina-db]
        sudo_allowed: false
        max_runtime_minutes: 15
        max_tokens_per_run: 20000
        max_usd_per_day: 1.5
        needs_approval_for: []
""")


@pytest.fixture
def tmp_policies(tmp_path: Path) -> Path:
    p = tmp_path / "policies.yaml"
    p.write_text(FULL_YAML)
    return p


def _make_spawner(tmp_policies, monkeypatch, mock_env=None):
    env = mock_env or {}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import lina_orchestrator.infrastructure.spawner as spawner_mod
    importlib.reload(spawner_mod)
    from lina_orchestrator.application.policy_service import PolicyService
    from lina_orchestrator.domain.policy import PolicyStore
    store = PolicyStore.from_yaml(tmp_policies)
    service = PolicyService(store)
    return spawner_mod.SpawnerService(service), spawner_mod


def _mock_db_conn(row=None):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    if row is not None:
        mock_cursor.fetchone.return_value = row
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


class TestDbHelpers:
    def test_db_conn_calls_psycopg2(self, tmp_policies, monkeypatch):
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch("psycopg2.connect", return_value=MagicMock()) as mock_connect:
            spawner_mod._db_conn()
        mock_connect.assert_called_once()

    def test_db_create_session(self, tmp_policies, monkeypatch):
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch("psycopg2.connect", return_value=_mock_db_conn()):
            spawner_mod._db_create_session("tid", "dev", "goal")

    @pytest.mark.parametrize("status", ["running", "completed", "failed", "killed"])
    def test_db_update_status(self, tmp_policies, monkeypatch, status):
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch("psycopg2.connect", return_value=_mock_db_conn()):
            spawner_mod._db_update_status("tid", status, result_summary="done")

    def test_db_append_event(self, tmp_policies, monkeypatch):
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch("psycopg2.connect", return_value=_mock_db_conn()):
            spawner_mod._db_append_event("tid", "heartbeat", {"ping": True})


class TestSpawnerServiceSpawn:
    def test_spawn_returns_pending(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"id": "sess-001", "session_id": "sess-001"}
        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
        ):
            result = spawner.spawn("dev", "tarea")
        assert result["status"] == "pending"
        assert result["goosed_session_id"] == "sess-001"
        assert len(result["agent_id"]) == 32

    def test_spawn_sends_request(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(
            tmp_policies, monkeypatch, {"LINA_GOOSED_URL": "http://t:3000"}
        )
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"id": "sess-001"}
        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp) as mock_post,
        ):
            spawner.spawn("dev", "task")
        mock_post.assert_any_call(
            "http://t:3000/agent/start",
            json={"working_dir": "/tmp"},
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=30,
        )

    def test_spawn_unknown_role(self, tmp_policies, monkeypatch):
        spawner, _ = _make_spawner(tmp_policies, monkeypatch)
        with pytest.raises(ValueError, match="Rol desconocido"):
            spawner.spawn("nonexistent", "goal")

    def test_spawn_connection_error(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", side_effect=requests.ConnectionError("no route")),
            pytest.raises(RuntimeError, match="No se pudo crear sesion"),
        ):
            spawner.spawn("dev", "goal")

    def test_spawn_http_error(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_resp = MagicMock(status_code=500)
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
            pytest.raises(RuntimeError, match="No se pudo crear sesion"),
        ):
            spawner.spawn("dev", "goal")


class TestSpawnerServiceKill:
    def test_kill_unknown(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn()):
            result = spawner.kill("unknown")
        assert result["killed"] is False

    def test_kill_running(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with (
            patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn({"status": "running"})),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.kill("running-agent")
        assert result["killed"] is True


class TestSpawnerGetStatus:
    def test_not_found(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        with patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn()):
            result = spawner.get_status("x")
        assert "error" in result

    def test_returns_fields(self, tmp_policies, monkeypatch):
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        row = {"id": "aabbccdd" * 4, "role": "dev", "goal": "x",
               "status": "running", "pid": None, "elapsed_seconds": 10}
        with patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn(row)):
            result = spawner.get_status("aabbccdd" * 4)
        assert result["status"] == "running"
        assert result["process_alive"] is False
        assert result["agent_id"] == "aabbccdd" * 4


class TestSystemPrompt:
    def test_includes_polling(self, tmp_policies, monkeypatch):
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        prompt = spawner_mod._build_system_prompt("dev", "hacer X", "sess-001")
        assert "get_pending_instructions" in prompt
        assert "append_agent_event" in prompt
        assert "update_agent_status" in prompt
        assert "session_id='sess-001'" in prompt


class TestGoosedHeaders:
    def test_with_secret(self, tmp_policies, monkeypatch):
        monkeypatch.setenv("LINA_GOOSED_SECRET", "my-key")
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        assert spawner_mod._goosed_headers()["x-secret-key"] == "my-key"


@pytest.fixture
def reloaded_server(tmp_policies, monkeypatch):
    monkeypatch.setenv("LINA_POLICIES_FILE", str(tmp_policies))
    import lina_orchestrator.infrastructure.spawner as spawner_mod
    import lina_orchestrator.server as srv
    importlib.reload(spawner_mod)
    importlib.reload(srv)
    srv._service = None
    srv._spawner = None
    yield srv
    srv._service = None
    srv._spawner = None


class TestServerSpawnTool:
    def test_spawn_agent_returns_pending(self, reloaded_server, monkeypatch):
        srv = reloaded_server
        import lina_orchestrator.infrastructure.spawner as spawner_mod
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"id": "sess-001"}
        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
        ):
            result = srv.spawn_agent("dev", "test goal")
        assert result.get("status") == "pending"
        assert "agent_id" in result

    def test_spawn_agent_unknown_role(self, reloaded_server):
        assert "error" in reloaded_server.spawn_agent("superadmin", "goal")

    def test_kill_agent_not_found(self, reloaded_server):
        import lina_orchestrator.infrastructure.spawner as spawner_mod
        with patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn()):
            assert reloaded_server.kill_agent("x")["killed"] is False

    def test_list_agents_db_error(self, reloaded_server):
        import lina_orchestrator.infrastructure.spawner as spawner_mod
        with patch.object(spawner_mod, "_db_conn", side_effect=Exception("DB down")):
            result = reloaded_server.list_agents()
        assert "error" in result[0]

    def test_send_instruction_happy_path(self, reloaded_server):
        import lina_orchestrator.infrastructure.spawner as spawner_mod
        fake_row = {"id": 42, "sent_at": datetime(2026, 1, 1, tzinfo=UTC)}
        with (
            patch.object(spawner_mod, "_db_conn", return_value=_mock_db_conn(fake_row)),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = reloaded_server.send_instruction("agent-abc", "nueva instruccion")
        assert result["command_id"] == 42


class TestServerPolicyTools:
    def test_list_roles(self, reloaded_server):
        assert "dev" in reloaded_server.list_roles()

    def test_check_mcp_allowed(self, reloaded_server):
        assert reloaded_server.check_mcp_allowed("dev", "lina-fs-safe")["allowed"] is True
        assert reloaded_server.check_mcp_allowed("dev", "lina-moodle")["allowed"] is False

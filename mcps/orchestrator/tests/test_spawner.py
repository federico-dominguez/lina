"""Tests para SpawnerService y server tools (issue #84, #85).

El nuevo SpawnerService usa HTTP API de goosed (requests.post) en vez de
subprocess.Popen. No hay config YAML, no hay tracking de procesos nativos,
no hay señales SIGTERM/SIGKILL — todo se maneja via goosed HTTP API + DB.

Estrategia:
  - SpawnerService usa requests.post y psycopg2; ambos se mockean.
  - Las tools de server.py se verifican con spawner mockeado.
  - Coverage target: 80% (excluyendo líneas de SSE streaming no testeables
    en unit tests porque requieren un goosed real).
"""

from __future__ import annotations

import importlib
import textwrap
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_spawner(
    tmp_policies: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_env: dict | None = None,
):
    """Crea un SpawnerService con DB mockeada para tests unitarios.

    Args:
        mock_env: dict de env vars adicionales a setear (ej: LINA_GOOSED_URL).
    """
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


def _mock_db_conn(row: dict | None = None) -> MagicMock:
    """Crea un mock de conexión psycopg2 con opción de fila de retorno."""
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


# ─── DB helpers tests ─────────────────────────────────────────────────────────


class TestDbHelpers:
    """Tests directos de los helpers de DB públicos de spawner.py."""

    def test_db_conn_calls_psycopg2(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_conn = MagicMock()
        with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            conn = spawner_mod._db_conn()
        mock_connect.assert_called_once()
        assert conn is mock_conn

    def test_db_create_session(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_conn = _mock_db_conn()

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_create_session("testid123", "dev", "goal text")

        mock_cursor = mock_conn.cursor.return_value
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO agent_sessions" in sql

    @pytest.mark.parametrize("status", ["running", "completed", "failed", "killed"])
    def test_db_update_status(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_conn = _mock_db_conn()

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_update_status("testid", status, result_summary="done")

        sql = mock_conn.cursor.return_value.execute.call_args[0][0]
        if status == "running":
            assert "started_at" in sql
        if status in ("completed", "failed", "killed"):
            assert "ended_at" in sql

    def test_db_append_event(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        mock_conn = _mock_db_conn()

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_append_event("testid", "heartbeat", {"ping": True})

        sql = mock_conn.cursor.return_value.execute.call_args[0][0]
        assert "INSERT INTO agent_events" in sql


# ─── SpawnerService.spawn() tests ─────────────────────────────────────────────


class TestSpawnerServiceSpawn:
    """Tests para SpawnerService.spawn() con HTTP API mockeada."""

    def test_spawn_returns_agent_id_and_pending_status(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn() devuelve agent_id, goosed_session_id y status=pending."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "goosed-abc-123", "session_id": "goosed-abc-123"}

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
        ):
            result = spawner.spawn("dev", "Listar archivos del repo")

        assert result["status"] == "pending"
        assert result["goosed_session_id"] == "goosed-abc-123"
        assert "agent_id" in result
        assert len(result["agent_id"]) == 32
        assert result["role"] == "dev"

    def test_spawn_sends_agent_start_request(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn() debe llamar POST /agent/start con working_dir."""
        spawner, spawner_mod = _make_spawner(
            tmp_policies, monkeypatch, {"LINA_GOOSED_URL": "http://test-goosed:3000"}
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "sess-001"}

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp) as mock_post,
        ):
            spawner.spawn("dev", "task")

        # Verificar que se llamó a /agent/start
        mock_post.assert_any_call(
            "http://test-goosed:3000/agent/start",
            json={"working_dir": "/tmp"},
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=30,
        )

    def test_spawn_creates_db_session(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn() debe crear la sesión en DB antes de llamar a goosed."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "sess-001"}

        with (
            patch.object(spawner_mod, "_db_create_session") as mock_create,
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
        ):
            spawner.spawn("dev", "task")

        mock_create.assert_called_once()
        args = mock_create.call_args[0]
        assert len(args[0]) == 32  # UUID hex
        assert args[1] == "dev"
        assert args[2] == "task"

    def test_spawn_unknown_role_raises_error(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn() con rol desconocido lanza ValueError sin llamar a goosed."""
        spawner, _ = _make_spawner(tmp_policies, monkeypatch)

        with pytest.raises(ValueError, match="Rol desconocido"):
            spawner.spawn("nonexistent", "goal")

    def test_spawn_goosed_not_available(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si goosed no responde, spawn() lanza RuntimeError."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", side_effect=requests.ConnectionError("goosed unreachable")),
            pytest.raises(RuntimeError, match="No se pudo crear sesión"),
        ):
            spawner.spawn("dev", "goal")

    def test_spawn_goosed_returns_error_status(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si goosed devuelve HTTP error, spawn() lanza RuntimeError."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
            pytest.raises(RuntimeError, match="No se pudo crear sesión"),
        ):
            spawner.spawn("dev", "goal")


# ─── SpawnerService.kill() tests ──────────────────────────────────────────────


class TestSpawnerServiceKill:
    """Tests para SpawnerService.kill() — ya no hay señales, solo DB."""

    def test_kill_unknown_agent(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kill() de agente que no existe en DB devuelve killed=False."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_conn = _mock_db_conn(None)

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.kill("nonexistent-agent")

        assert result["killed"] is False
        assert "no encontrada" in result["message"]

    def test_kill_already_terminated(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kill() de agente ya terminado devuelve killed=False."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_conn = _mock_db_conn({"status": "completed"})

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.kill("completed-agent")

        assert result["killed"] is False
        assert "ya terminado" in result["message"]

    def test_kill_running_agent(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kill() sobre agente en running marca killed en DB."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_conn = _mock_db_conn({"status": "running"})

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_update_status") as mock_update,
            patch.object(spawner_mod, "_db_append_event") as mock_event,
        ):
            result = spawner.kill("running-agent")

        assert result["killed"] is True
        mock_update.assert_called_with("running-agent", "killed")
        mock_event.assert_called_once()

    def test_kill_pending_agent(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kill() sobre agente en pending también marca killed."""
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_conn = _mock_db_conn({"status": "pending"})

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.kill("pending-agent")

        assert result["killed"] is True


# ─── SpawnerService.get_status() tests ────────────────────────────────────────


class TestSpawnerGetStatus:
    """Tests para get_status() — siempre process_alive=False en HTTP mode."""

    def test_session_not_found(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        mock_conn = _mock_db_conn(None)

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status("nonexistent")

        assert "error" in result

    def test_returns_db_fields(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        row = {
            "id": "abc123" * 4,
            "role": "dev",
            "goal": "test task",
            "status": "running",
            "pid": None,
            "elapsed_seconds": 42,
        }
        mock_conn = _mock_db_conn(row)

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status("abc123" * 4)

        assert result["agent_id"] == "abc123" * 4
        assert result["role"] == "dev"
        assert result["status"] == "running"
        assert result["process_alive"] is False
        assert result["elapsed_seconds"] == 42

    def test_status_completed(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spawner, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        row = {
            "id": "deadbeef" * 4,
            "role": "research",
            "goal": "research task",
            "status": "completed",
            "pid": None,
            "elapsed_seconds": 300,
        }
        mock_conn = _mock_db_conn(row)

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status("deadbeef" * 4)

        assert result["status"] == "completed"
        assert result["process_alive"] is False


# ─── _build_system_prompt tests ───────────────────────────────────────────────


class TestSystemPrompt:
    """Tests para _build_system_prompt — el prompt que recibe cada sub-agente."""

    def test_mentions_role_and_goal(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        prompt = spawner_mod._build_system_prompt("dev", "hacer X", "session-abc")

        assert "dev" in prompt
        assert "hacer X" in prompt
        assert "session-abc" in prompt

    def test_includes_polling_instructions(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        prompt = spawner_mod._build_system_prompt("dev", "tarea", "sess-001")

        assert "get_pending_instructions" in prompt
        assert "append_agent_event" in prompt
        assert "update_agent_status" in prompt

    def test_includes_session_id_in_polling_instruction(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)

        prompt = spawner_mod._build_system_prompt("research", "investigar", "my-session-42")

        assert "session_id='my-session-42'" in prompt


# ─── _goosed_headers tests ────────────────────────────────────────────────────


class TestGoosedHeaders:
    """Tests para _goosed_headers() — cabeceras HTTP para goosed API."""

    def test_without_secret(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        headers = spawner_mod._goosed_headers()
        assert headers["Content-Type"] == "application/json"
        assert "x-secret-key" not in headers

    def test_with_secret(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINA_GOOSED_SECRET", "my-secret-key")
        _, spawner_mod = _make_spawner(tmp_policies, monkeypatch)
        headers = spawner_mod._goosed_headers()
        assert headers["x-secret-key"] == "my-secret-key"


# ─── Server tools tests ───────────────────────────────────────────────────────


@pytest.fixture
def reloaded_server(
    tmp_policies: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Carga server.py con policies en modo test (sin spawner real)."""
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
    """Tests de la tool spawn_agent en server.py."""

    def test_spawn_agent_returns_pending(
        self, reloaded_server: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn_agent() devuelve {agent_id, status=pending}."""
        srv = reloaded_server

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "sess-001"}

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("requests.post", return_value=mock_resp),
        ):
            result = srv.spawn_agent("dev", "test goal")

        assert result.get("status") == "pending"
        assert "agent_id" in result
        assert "goosed_session_id" in result

    def test_spawn_agent_unknown_role(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        result = srv.spawn_agent("superadmin", "goal")
        assert "error" in result

    def test_kill_agent_not_found(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        mock_conn = _mock_db_conn(None)

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = srv.kill_agent("nonexistent")

        assert result["killed"] is False

    def test_list_agents_handles_db_error(
        self, reloaded_server: types.ModuleType
    ) -> None:
        srv = reloaded_server

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with patch.object(spawner_mod, "_db_conn", side_effect=Exception("DB down")):
            result = srv.list_agents()

        assert len(result) == 1
        assert "error" in result[0]

    def test_send_instruction_handles_db_error(
        self, reloaded_server: types.ModuleType
    ) -> None:
        srv = reloaded_server

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with patch.object(spawner_mod, "_db_conn", side_effect=Exception("DB down")):
            result = srv.send_instruction("agent-abc", "nueva instrucción")

        assert "error" in result

    def test_send_instruction_happy_path(
        self, reloaded_server: types.ModuleType
    ) -> None:
        srv = reloaded_server

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        fake_ts = datetime(2026, 1, 1, tzinfo=UTC)
        fake_row = {"id": 42, "sent_at": fake_ts}

        mock_conn = _mock_db_conn(fake_row)

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = srv.send_instruction("agent-abc", "nueva instrucción")

        assert result["command_id"] == 42
        assert result["agent_id"] == "agent-abc"
        assert result["kind"] == "send_instruction"
        assert "2026-01-01" in result["sent_at"]


# ─── Policy tools tests (sin spawner) ─────────────────────────────────────────


class TestServerPolicyTools:
    """Tests de las tools de políticas (no requieren spawner)."""

    def test_list_roles(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        roles = srv.list_roles()
        assert "dev" in roles
        assert "research" in roles

    def test_get_role(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        role = srv.get_role("dev")
        assert role["allowed_mcps"] == ["lina-fs-safe", "lina-github"]

    def test_get_role_unknown(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        result = srv.get_role("nonexistent")
        assert "error" in result
        assert "known_roles" in result

    def test_check_mcp_allowed(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        assert srv.check_mcp_allowed("dev", "lina-fs-safe")["allowed"] is True
        assert srv.check_mcp_allowed("dev", "lina-moodle")["allowed"] is False

    def test_check_requires_approval(self, reloaded_server: types.ModuleType) -> None:
        srv = reloaded_server
        assert srv.check_requires_approval("dev", "pr_merge")["requires_approval"] is True
        assert srv.check_requires_approval("research", "pr_merge")["requires_approval"] is False

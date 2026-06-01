"""Tests para SpawnerService y las nuevas tools de server.py (issues #84, #85).

Estrategia:
  - SpawnerService usa subprocess.Popen y psycopg2; ambos se mockean.
  - Las tools de server.py (spawn_agent, kill_agent, etc.) se verifican con
    el spawner mockeado para no necesitar DB ni goosed en CI.
"""

from __future__ import annotations

import importlib
import textwrap
import types
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

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

BASE_CONFIG_YAML = textwrap.dedent("""\
    GOOSE_MAX_TURNS: 100
    extensions:
      developer:
        enabled: true
        type: builtin
        name: developer
      lina-fs-safe:
        name: lina-fs-safe
        type: streamable_http
        uri: http://lina-mcp-gateway:8102/mcp
        enabled: true
        timeout: 60
      lina-github:
        name: lina-github
        type: streamable_http
        uri: http://lina-mcp-gateway:8107/mcp
        enabled: true
        timeout: 60
      lina-db:
        name: lina-db
        type: streamable_http
        uri: http://lina-mcp-gateway:8106/mcp
        enabled: true
        timeout: 30
""")


@pytest.fixture
def tmp_policies(tmp_path: Path) -> Path:
    p = tmp_path / "policies.yaml"
    p.write_text(FULL_YAML)
    return p


@pytest.fixture
def tmp_base_config(tmp_path: Path) -> Path:
    p = tmp_path / "goose.config.yaml"
    p.write_text(BASE_CONFIG_YAML)
    return p


# ─── SpawnerService unit tests ────────────────────────────────────────────────


class TestConfigGeneration:
    """Tests para _generate_agent_config (no requiere DB ni proceso)."""

    def test_filters_mcps_by_role(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """La config generada para 'dev' solo incluye MCPs permitidos por el rol."""
        import yaml

        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        # Import tardío para que tome los env vars mockeados.
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        config_path = spawner_mod._generate_agent_config(
            "test-session-001",
            "dev",
            ["lina-fs-safe", "lina-github"],
            {"max_runtime_minutes": 30},
        )

        assert config_path.exists()
        generated = yaml.safe_load(config_path.read_text())
        extensions = generated["extensions"]

        # developer es built-in, siempre presente
        assert "developer" in extensions
        # MCPs del rol dev
        assert "lina-fs-safe" in extensions
        assert "lina-github" in extensions
        # lina-db NO está en allowed_mcps de dev
        assert "lina-db" not in extensions

    def test_research_role_only_has_lina_db(
        self, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El rol 'research' solo puede usar lina-db."""
        import yaml

        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        config_path = spawner_mod._generate_agent_config(
            "test-session-002",
            "research",
            ["lina-db"],
            {"max_runtime_minutes": 15},
        )
        generated = yaml.safe_load(config_path.read_text())
        extensions = generated["extensions"]

        assert "lina-db" in extensions
        assert "lina-fs-safe" not in extensions
        assert "lina-github" not in extensions

    def test_missing_base_config_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si la config base no existe, debe lanzar FileNotFoundError."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", "/nonexistent/config.yaml")
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        with pytest.raises(FileNotFoundError, match="Config base de goosed"):
            spawner_mod._generate_agent_config("test-003", "dev", [], {"max_runtime_minutes": 30})


class TestSpawnerServiceSpawn:
    """Tests para SpawnerService.spawn() con Popen mockeado."""

    def _make_spawner(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Crea un SpawnerService con DB y subprocess mockeados."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        from lina_orchestrator.application.policy_service import PolicyService
        from lina_orchestrator.domain.policy import PolicyStore

        store = PolicyStore.from_yaml(tmp_policies)
        service = PolicyService(store)
        return spawner_mod.SpawnerService(service), spawner_mod

    def test_spawn_returns_agent_id_and_pid(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """spawn() devuelve agent_id, pid y status=running."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42000

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            result = spawner.spawn("dev", "Listar archivos del repo")

        assert result["status"] == "running"
        assert result["pid"] == 42000
        assert "agent_id" in result
        assert len(result["agent_id"]) == 32  # UUID hex sin guiones
        assert result["role"] == "dev"

    def test_spawn_unknown_role_returns_error(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """spawn() con rol desconocido devuelve error sin llamar a Popen."""
        spawner, _ = self._make_spawner(tmp_policies, tmp_base_config, tmp_path, monkeypatch)

        with pytest.raises(ValueError, match="Rol desconocido"):
            spawner.spawn("nonexistent", "goal")

    def test_spawn_goosed_not_found(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si goosed no está en PATH, spawn() lanza RuntimeError."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("subprocess.Popen", side_effect=FileNotFoundError("goosed not found")),
            pytest.raises(RuntimeError, match="goosed no encontrado"),
        ):
            spawner.spawn("dev", "goal")


class TestSpawnerServiceKill:
    """Tests para SpawnerService.kill()."""

    def test_kill_running_process(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """kill() sobre un proceso en _processes devuelve killed=True."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        from lina_orchestrator.application.policy_service import PolicyService
        from lina_orchestrator.domain.policy import PolicyStore

        store = PolicyStore.from_yaml(tmp_policies)
        service = PolicyService(store)
        spawner = spawner_mod.SpawnerService(service)

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.wait.return_value = None  # terminó en el grace period

        agent_id = "deadbeef" * 4

        # Simular que el proceso está registrado
        spawner._processes[agent_id] = mock_proc

        with (
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.kill(agent_id)

        assert result["killed"] is True
        # proc.terminate() was replaced by os.killpg — verify kill() succeeded
        assert agent_id not in spawner._processes

    def test_kill_unknown_agent(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """kill() de agente desconocido (sin proceso en memoria y sin DB) devuelve killed=False."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        from lina_orchestrator.application.policy_service import PolicyService
        from lina_orchestrator.domain.policy import PolicyStore

        store = PolicyStore.from_yaml(tmp_policies)
        service = PolicyService(store)
        spawner = spawner_mod.SpawnerService(service)

        # Mock DB que devuelve None (sesión no encontrada)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.kill("nonexistent-agent-id")

        assert result["killed"] is False
        assert "no encontrada" in result["message"]


# ─── Server tools tests ───────────────────────────────────────────────────────


@pytest.fixture
def reloaded_server_with_spawner(
    tmp_policies: Path,
    tmp_base_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Carga server.py con policies y spawner en modo test."""
    monkeypatch.setenv("LINA_POLICIES_FILE", str(tmp_policies))
    monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
    monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

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

    def test_spawn_agent_returns_running(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """spawn_agent() devuelve {agent_id, status=running} cuando Popen tiene éxito."""
        srv = reloaded_server_with_spawner

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with (
            patch.object(spawner_mod, "_db_create_session"),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            result = srv.spawn_agent("dev", "test goal")

        assert result.get("status") == "running"
        assert "agent_id" in result

    def test_spawn_agent_unknown_role(self, reloaded_server_with_spawner: types.ModuleType) -> None:
        """spawn_agent() con rol inválido devuelve {"error": ...}."""
        srv = reloaded_server_with_spawner
        result = srv.spawn_agent("superadmin", "goal")
        assert "error" in result

    def test_kill_agent_not_found(self, reloaded_server_with_spawner: types.ModuleType) -> None:
        """kill_agent() de agente desconocido devuelve killed=False."""
        srv = reloaded_server_with_spawner

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = srv.kill_agent("nonexistent")

        assert result["killed"] is False

    def test_list_agents_handles_db_error(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """list_agents() con DB caída devuelve lista con error en vez de crashear."""
        srv = reloaded_server_with_spawner

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with patch.object(spawner_mod, "_db_conn", side_effect=Exception("DB down")):
            result = srv.list_agents()

        assert len(result) == 1
        assert "error" in result[0]

    def test_send_instruction_handles_db_error(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """send_instruction() con DB caída devuelve {"error": ...}."""
        srv = reloaded_server_with_spawner

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        with patch.object(spawner_mod, "_db_conn", side_effect=Exception("DB down")):
            result = srv.send_instruction("agent-abc", "nueva instrucción")

        assert "error" in result

    def test_send_instruction_happy_path(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """send_instruction() persiste el comando en DB y retorna metadatos."""
        from datetime import datetime

        srv = reloaded_server_with_spawner

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        fake_ts = datetime(2026, 1, 1, tzinfo=UTC)
        fake_row = {"id": 42, "sent_at": fake_ts}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = fake_row
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = srv.send_instruction("agent-abc", "nueva instrucción")

        assert result["command_id"] == 42
        assert result["agent_id"] == "agent-abc"
        assert result["kind"] == "send_instruction"
        assert "2026-01-01" in result["sent_at"]
        mock_conn.commit.assert_called_once()


# ─── DB helpers unit tests ────────────────────────────────────────────────────


class TestDbHelpers:
    """Tests directos de los helpers de DB (cobertura de líneas 93-152)."""

    def test_db_conn_calls_psycopg2(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        mock_conn = MagicMock()
        with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            conn = spawner_mod._db_conn()
        mock_connect.assert_called_once()
        assert conn is mock_conn

    def test_db_create_session(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_create_session("testid123", "dev", "goal text")

        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO agent_sessions" in sql

    def test_db_update_status_running(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_update_status("testid123", "running", pid=999)

        sql = mock_cursor.execute.call_args[0][0]
        assert "started_at" in sql

    def test_db_update_status_terminal(
        self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_update_status("testid123", "completed", result_summary="done")

        sql = mock_cursor.execute.call_args[0][0]
        assert "ended_at" in sql

    def test_db_append_event(self, tmp_policies: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            spawner_mod._db_append_event("testid123", "heartbeat", {"ping": True})

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO agent_events" in sql


# ─── SpawnerService.get_status tests ─────────────────────────────────────────


def _make_spawner_for_status(
    tmp_policies: Path,
    tmp_base_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
    monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

    import lina_orchestrator.infrastructure.spawner as spawner_mod

    importlib.reload(spawner_mod)

    from lina_orchestrator.application.policy_service import PolicyService
    from lina_orchestrator.domain.policy import PolicyStore

    store = PolicyStore.from_yaml(tmp_policies)
    service = PolicyService(store)
    return spawner_mod.SpawnerService(service), spawner_mod


class TestSpawnerGetStatus:
    def _mock_conn(self, row):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = row
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn

    def test_session_not_found(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spawner, spawner_mod = _make_spawner_for_status(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )
        mock_conn = self._mock_conn(None)
        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status("nonexistent")
        assert "error" in result

    def test_status_running_process_alive(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import datetime

        spawner, spawner_mod = _make_spawner_for_status(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )
        agent_id = "cafebabe" * 4

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        spawner._processes[agent_id] = mock_proc

        row = {
            "id": agent_id,
            "role": "dev",
            "goal": "work",
            "status": "running",
            "pid": 12345,
            "started_at": datetime.now(tz=UTC),
            "elapsed_seconds": 100,
        }
        mock_conn = self._mock_conn(row)
        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status(agent_id)

        assert result["process_alive"] is True
        assert result["status"] == "running"

    def test_status_running_process_died(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import datetime

        spawner, spawner_mod = _make_spawner_for_status(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )
        agent_id = "deadbeef" * 4

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited with code 0
        spawner._processes[agent_id] = mock_proc

        row = {
            "id": agent_id,
            "role": "dev",
            "goal": "work",
            "status": "running",
            "pid": 99,
            "started_at": datetime.now(tz=UTC),
            "elapsed_seconds": 200,
        }
        mock_conn = self._mock_conn(row)
        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.get_status(agent_id)

        assert result["process_alive"] is False
        assert result["status"] == "completed"
        assert agent_id not in spawner._processes

    def test_status_process_not_in_memory_alive_via_os_kill(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si el proc no está en memoria pero os.kill(pid, 0) no lanza, está vivo."""
        import os as _os
        from datetime import datetime

        spawner, spawner_mod = _make_spawner_for_status(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )
        agent_id = "aabbccdd" * 4

        row = {
            "id": agent_id,
            "role": "dev",
            "goal": "work",
            "status": "running",
            "pid": 77777,
            "started_at": datetime.now(tz=UTC),
            "elapsed_seconds": 50,
        }
        mock_conn = self._mock_conn(row)
        # Parchar os.kill en el namespace del módulo spawner (no globalmente)
        mock_os = MagicMock(spec=_os)
        mock_os.kill.return_value = None  # no lanza → proceso vivo
        monkeypatch.setattr(spawner_mod, "os", mock_os)
        with patch.object(spawner_mod, "_db_conn", return_value=mock_conn):
            result = spawner.get_status(agent_id)

        assert result["process_alive"] is True
        assert result["status"] == "running"

    def test_status_process_not_in_memory_dead_via_os_kill(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si el proc no está en memoria y os.kill(pid, 0) lanza, está muerto."""
        import os as _os
        from datetime import datetime

        spawner, spawner_mod = _make_spawner_for_status(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )
        agent_id = "11223344" * 4

        row = {
            "id": agent_id,
            "role": "dev",
            "goal": "work",
            "status": "running",
            "pid": 88888,
            "started_at": datetime.now(tz=UTC),
            "elapsed_seconds": 10,
        }
        mock_conn = self._mock_conn(row)
        mock_os = MagicMock(spec=_os)
        mock_os.kill.side_effect = ProcessLookupError  # proceso muerto
        monkeypatch.setattr(spawner_mod, "os", mock_os)
        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_conn),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.get_status(agent_id)

        assert result["process_alive"] is False
        assert result["status"] == "failed"


# ─── SpawnerService.kill timeout test ────────────────────────────────────────


class TestSpawnerKillTimeout:
    def test_kill_sends_sigkill_on_timeout(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si el proceso no termina en el grace period, se envía SIGKILL al grupo."""
        import os as _os
        import subprocess

        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        from lina_orchestrator.application.policy_service import PolicyService
        from lina_orchestrator.domain.policy import PolicyStore

        store = PolicyStore.from_yaml(tmp_policies)
        service = PolicyService(store)
        spawner = spawner_mod.SpawnerService(service)

        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="goosed", timeout=5)
        agent_id = "ffffffff" * 4
        spawner._processes[agent_id] = mock_proc

        # Parchar os.killpg en el namespace del módulo spawner
        mock_os = MagicMock(spec=_os)
        mock_os.killpg.return_value = None
        monkeypatch.setattr(spawner_mod, "os", mock_os)

        with (
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            result = spawner.kill(agent_id)

        assert result["killed"] is True
        # SIGKILL fue enviado al grupo del proceso
        sigkill = spawner_mod.signal.SIGKILL
        mock_os.killpg.assert_any_call(mock_proc.pid, sigkill)

    def test_cleanup_config_removes_file(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_cleanup_config() borra el archivo de configuración temporal."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        # Crear un archivo simulado de config
        config_dir = tmp_path / "agents"
        config_dir.mkdir(parents=True, exist_ok=True)
        agent_id = "abcd1234" * 4
        config_file = config_dir / f"agent-{agent_id}.yaml"
        config_file.write_text("test: true")

        spawner_mod.SpawnerService._cleanup_config(agent_id)
        assert not config_file.exists()

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
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La config generada para 'dev' solo incluye MCPs permitidos por el rol."""
        import yaml

        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        # Import tardío para que tome los env vars mockeados.
        import lina_orchestrator.infrastructure.spawner as spawner_mod

        importlib.reload(spawner_mod)

        config_path = spawner_mod._generate_agent_config(
            "test-session-001", "dev", ["lina-fs-safe", "lina-github"]
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
            "test-session-002", "research", ["lina-db"]
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
            spawner_mod._generate_agent_config("test-003", "dev", [])


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
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn() con rol desconocido devuelve error sin llamar a Popen."""
        spawner, _ = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        with pytest.raises(ValueError, match="Rol desconocido"):
            spawner.spawn("nonexistent", "goal")

    def test_spawn_goosed_not_found(
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        mock_proc.terminate.assert_called_once()
        assert agent_id not in spawner._processes

    def test_kill_unknown_agent(
        self, tmp_policies: Path, tmp_base_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    def test_spawn_agent_unknown_role(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """spawn_agent() con rol inválido devuelve {"error": ...}."""
        srv = reloaded_server_with_spawner
        result = srv.spawn_agent("superadmin", "goal")
        assert "error" in result

    def test_kill_agent_not_found(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
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


# ─── Watchdog timeout tests ─────────────────────────────────────────────────


class TestWatchdog:
    """Tests para el watchdog de timeouts (issue #89).

    Estrategia:
      - Se mockea _db_conn para que devuelva agentes en estado 'running' con
        elapsed_seconds conocidos.
      - Se mockea subprocess.Popen y os.kill para evitar efectos laterales.
      - _enforce_timeouts() se llama directamente (sin el thread loop) para
        poder verificar el comportamiento sincrónicamente.
    """

    def _make_spawner(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        auto_start_watchdog: bool = False,
    ):
        """Crea SpawnerService sin watchdog para tests sincrónicos."""
        monkeypatch.setenv("LINA_BASE_GOOSE_CONFIG", str(tmp_base_config))
        monkeypatch.setenv("LINA_AGENT_CONFIG_DIR", str(tmp_path / "agents"))

        import lina_orchestrator.infrastructure.spawner as spawner_mod
        importlib.reload(spawner_mod)

        from lina_orchestrator.application.policy_service import PolicyService
        from lina_orchestrator.domain.policy import PolicyStore

        store = PolicyStore.from_yaml(tmp_policies)
        service = PolicyService(store)
        return spawner_mod.SpawnerService(service, auto_start_watchdog=auto_start_watchdog), spawner_mod

    # ─── helpers para mockear DB ──────────────────────────────────────────

    @staticmethod
    def _make_db_running_agents(agents: list[dict]) -> MagicMock:
        """Crea un mock de conexión DB que devuelve agentes en 'running'.

        Cada agente debe tener: id, role, pid, elapsed_seconds.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        # Simular RealDictCursor: cada fila es un dict convertible
        def fetchall() -> list[dict]:
            return agents

        mock_cursor.fetchall.side_effect = fetchall
        mock_cursor.__iter__ = MagicMock(return_value=iter(agents))
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn

    # ─── _enforce_timeouts ────────────────────────────────────────────────

    def test_enforce_kills_overdue_agent(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agente con elapsed >> max_runtime_minutes debe ser matado."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        # dev tiene max_runtime_minutes=30 → 1800s
        mock_db = self._make_db_running_agents([
            {"id": "agent-overdue", "role": "dev", "pid": 12345, "elapsed_seconds": 99999},
        ])

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.return_value = None
        spawner._processes["agent-overdue"] = mock_proc

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db),
            patch.object(spawner_mod, "_db_update_status") as mock_update,
            patch.object(spawner_mod, "_db_append_event") as mock_event,
        ):
            timed_out = spawner._enforce_timeouts()

        assert len(timed_out) == 1
        assert timed_out[0]["agent_id"] == "agent-overdue"
        assert timed_out[0]["role"] == "dev"
        assert timed_out[0]["max_runtime_minutes"] == 30
        assert "agent-overdue" not in spawner._processes

        # Verificar que se llamó a DB con status='timeout'
        mock_update.assert_called_once_with(
            "agent-overdue", "timeout", result_summary=mock_update.call_args[1]["result_summary"]
        )
        assert "timeout" in mock_update.call_args[0][1]
        # Verificar que se logueó el evento
        mock_event.assert_called_once()
        assert mock_event.call_args[0][1] == "timeout"

    def test_enforce_skips_agent_within_limit(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agente con elapsed < max_runtime_minutes no debe ser molestado."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        # dev tiene max_runtime_minutes=30 → 1800s, elapsed=100s OK
        mock_db = self._make_db_running_agents([
            {"id": "agent-ok", "role": "dev", "pid": 12345, "elapsed_seconds": 100},
        ])

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        spawner._processes["agent-ok"] = mock_proc

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db),
            patch.object(spawner_mod, "_db_update_status") as mock_update,
            patch.object(spawner_mod, "_db_append_event") as mock_event,
        ):
            timed_out = spawner._enforce_timeouts()

        assert timed_out == []
        mock_update.assert_not_called()
        mock_event.assert_not_called()
        mock_proc.terminate.assert_not_called()
        assert "agent-ok" in spawner._processes

    def test_enforce_respects_different_role_limits(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Roles distintos tienen límites distintos — verificar ambos."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        # dev max=30min(1800s), research max=15min(900s)
        mock_db = self._make_db_running_agents([
            {"id": "dev-agent", "role": "dev", "pid": 111, "elapsed_seconds": 1000},
            # 1000s > 900s → research DEBE timeout
            {"id": "research-agent", "role": "research", "pid": 222, "elapsed_seconds": 1000},
        ])

        mock_proc_dev = MagicMock()
        mock_proc_dev.pid = 111
        mock_proc_dev.wait.return_value = None
        spawner._processes["dev-agent"] = mock_proc_dev

        mock_proc_research = MagicMock()
        mock_proc_research.pid = 222
        mock_proc_research.wait.return_value = None
        spawner._processes["research-agent"] = mock_proc_research

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            timed_out = spawner._enforce_timeouts()

        # Dev: 1000s < 1800s → OK. Research: 1000s > 900s → TIMEOUT
        timed_out_ids = [t["agent_id"] for t in timed_out]
        assert "dev-agent" not in timed_out_ids
        assert "research-agent" in timed_out_ids
        assert len(timed_out) == 1

    def test_enforce_skips_unknown_role(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agente con rol que ya no existe no debe crashear."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        mock_db = self._make_db_running_agents([
            {"id": "agent-ghost", "role": "deleted_role", "pid": 999, "elapsed_seconds": 99999},
        ])

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db),
            patch.object(spawner_mod, "_db_update_status") as mock_update,
            patch.object(spawner_mod, "_db_append_event") as mock_event,
        ):
            timed_out = spawner._enforce_timeouts()

        assert timed_out == []
        mock_update.assert_not_called()
        mock_event.assert_not_called()

    def test_enforce_kills_process_in_memory(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verificar que terminate() y wait() se llaman en el Popen correcto."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        mock_db = self._make_db_running_agents([
            {"id": "agent-killmem", "role": "research", "pid": 42, "elapsed_seconds": 9999},
        ])

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait.return_value = None
        spawner._processes["agent-killmem"] = mock_proc

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            spawner._enforce_timeouts()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert "agent-killmem" not in spawner._processes

    # ─── Thread lifecycle ─────────────────────────────────────────────────

    def test_start_watchdog_starts_thread(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start_watchdog() debe arrancar un thread daemon."""
        spawner, _ = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch,
            auto_start_watchdog=False,
        )

        assert spawner._watchdog_thread is None

        spawner._start_watchdog()

        assert spawner._watchdog_thread is not None
        assert spawner._watchdog_thread.is_alive()
        assert spawner._watchdog_thread.daemon is True
        assert spawner._watchdog_thread.name == "lina-watchdog"

        spawner.stop_watchdog()

    def test_stop_watchdog_joins_thread(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stop_watchdog() debe señalar y esperar al thread."""
        spawner, _ = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch,
            auto_start_watchdog=False,
        )

        spawner._start_watchdog()
        assert spawner._watchdog_thread.is_alive()

        spawner.stop_watchdog()
        assert not spawner._watchdog_thread.is_alive()

    def test_auto_start_on_init(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SpawnerService debe arrancar el watchdog automáticamente por defecto."""
        spawner, _ = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch,
            auto_start_watchdog=True,
        )

        assert spawner._watchdog_thread is not None
        assert spawner._watchdog_thread.is_alive()

        spawner.stop_watchdog()

    def test_start_watchdog_idempotent(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Llamar _start_watchdog() dos veces no debe crear dos threads."""
        spawner, _ = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch,
            auto_start_watchdog=False,
        )

        spawner._start_watchdog()
        thread1 = spawner._watchdog_thread

        spawner._start_watchdog()
        thread2 = spawner._watchdog_thread

        assert thread1 is thread2  # Misma referencia

        spawner.stop_watchdog()

    # ─── Contador de timeouts ─────────────────────────────────────────────

    def test_timeout_count_increments(
        self,
        tmp_policies: Path,
        tmp_base_config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El contador _timeout_count debe incrementarse con cada timeout."""
        spawner, spawner_mod = self._make_spawner(
            tmp_policies, tmp_base_config, tmp_path, monkeypatch
        )

        assert spawner._timeout_count == 0

        # Primera ronda: matar 2 agentes
        mock_db1 = self._make_db_running_agents([
            {"id": "a1", "role": "research", "pid": 1, "elapsed_seconds": 9999},
            {"id": "a2", "role": "research", "pid": 2, "elapsed_seconds": 9999},
        ])
        mock_proc1 = MagicMock()
        mock_proc1.pid = 1
        mock_proc1.wait.return_value = None
        mock_proc2 = MagicMock()
        mock_proc2.pid = 2
        mock_proc2.wait.return_value = None
        spawner._processes["a1"] = mock_proc1
        spawner._processes["a2"] = mock_proc2

        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db1),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            spawner._enforce_timeouts()

        assert spawner._timeout_count == 2

        # Segunda ronda: no hay agentes overdue
        mock_db2 = self._make_db_running_agents([
            {"id": "a3", "role": "research", "pid": 3, "elapsed_seconds": 10},
        ])
        with (
            patch.object(spawner_mod, "_db_conn", return_value=mock_db2),
            patch.object(spawner_mod, "_db_update_status"),
            patch.object(spawner_mod, "_db_append_event"),
        ):
            spawner._enforce_timeouts()

        assert spawner._timeout_count == 2  # No cambió


class TestServerWatchdogTool:
    """Tests para get_watchdog_status() en server.py."""

    def test_get_watchdog_status_returns_running(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """get_watchdog_status() debe devolver estado del watchdog."""
        srv = reloaded_server_with_spawner
        result = srv.get_watchdog_status()

        assert "running" in result
        assert "interval_seconds" in result
        assert "max_loop_errors" in result
        assert "agents_killed_by_timeout" in result
        assert result["agents_killed_by_timeout"] == 0

    def test_get_watchdog_status_after_timeout(
        self, reloaded_server_with_spawner: types.ModuleType
    ) -> None:
        """get_watchdog_status() debe reflejar el contador de timeouts."""
        srv = reloaded_server_with_spawner

        import lina_orchestrator.infrastructure.spawner as spawner_mod

        # Simular que se mataron 3 agentes
        srv._spw()._timeout_count = 3

        result = srv.get_watchdog_status()
        assert result["agents_killed_by_timeout"] == 3

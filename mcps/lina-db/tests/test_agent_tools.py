"""Tests unitarios para las 5 agent lifecycle tools en lina-db MCP (issue #83).

Estrategia: mockear _execute (igual que test_lina_db.py) para no necesitar DB real.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_execute(monkeypatch):
    """Mockea lina_db.server._execute para evitar conexión real a PostgreSQL."""
    mock = MagicMock(return_value=None)
    monkeypatch.setattr("lina_db.server._execute", mock)
    return mock


@pytest.fixture(autouse=True)
def silence_audit(monkeypatch):
    """Evita que _audit dispare queries reales durante los tests."""
    monkeypatch.setattr("lina_db.server._audit", MagicMock())


# ─── create_agent_session ─────────────────────────────────────────────────────


class TestCreateAgentSession:
    _VALID_ID = "a" * 32  # 32-char lowercase hex

    def test_creates_session_ok(self, mock_execute):
        from lina_db.server import create_agent_session

        now = datetime.now(tz=timezone.utc)
        # side_effect: INSERT → None, SELECT → row
        mock_execute.side_effect = [
            None,
            {
                "id": self._VALID_ID,
                "role": "dev",
                "goal": "test goal",
                "status": "pending",
                "created_at": now,
            },
        ]
        result = create_agent_session(self._VALID_ID, "dev", "test goal")
        assert result["status"] == "pending"
        assert result["id"] == self._VALID_ID
        assert isinstance(result["created_at"], str)  # serializado a ISO

    def test_empty_session_id_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="session_id"):
            create_agent_session("  ", "dev", "goal")

    def test_invalid_session_id_format_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="UUID hex"):
            create_agent_session("not-a-uuid", "dev", "goal")

    def test_session_id_with_uppercase_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="UUID hex"):
            create_agent_session("A" * 32, "dev", "goal")  # uppercase not valid

    def test_session_id_with_hyphens_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="UUID hex"):
            create_agent_session("aaaabbbb-cccc-dddd-eeee-ffffffffffff", "dev", "goal")

    def test_empty_role_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="role"):
            create_agent_session(self._VALID_ID, "  ", "goal")

    def test_empty_goal_raises(self, mock_execute):
        from lina_db.server import create_agent_session

        with pytest.raises(ValueError, match="goal"):
            create_agent_session(self._VALID_ID, "dev", "  ")

    def test_returns_empty_if_select_returns_none(self, mock_execute):
        from lina_db.server import create_agent_session

        mock_execute.side_effect = [None, None]
        result = create_agent_session(self._VALID_ID, "dev", "goal")
        assert result == {}


# ─── update_agent_status ──────────────────────────────────────────────────────


class TestUpdateAgentStatus:
    _VALID_ID = "b" * 32

    def _make_row(self, status: str):
        now = datetime.now(tz=timezone.utc)
        return {
            "id": self._VALID_ID,
            "role": "dev",
            "goal": "do stuff",
            "status": status,
            "pid": 42,
            "result_summary": None,
            "started_at": now,
            "ended_at": None,
            "updated_at": now,
        }

    def test_update_to_running(self, mock_execute):
        from lina_db.server import update_agent_status

        mock_execute.side_effect = [None, self._make_row("running")]
        result = update_agent_status(self._VALID_ID, "running", pid=42)
        assert result["status"] == "running"
        # timestamps serialized
        assert isinstance(result["updated_at"], str)

    def test_update_to_completed(self, mock_execute):
        from lina_db.server import update_agent_status

        row = self._make_row("completed")
        row["ended_at"] = datetime.now(tz=timezone.utc)
        mock_execute.side_effect = [None, row]
        result = update_agent_status(self._VALID_ID, "completed", result_summary="done")
        assert result["status"] == "completed"
        assert isinstance(result["ended_at"], str)

    def test_update_to_killed(self, mock_execute):
        from lina_db.server import update_agent_status

        row = self._make_row("killed")
        row["ended_at"] = datetime.now(tz=timezone.utc)
        mock_execute.side_effect = [None, row]
        result = update_agent_status(self._VALID_ID, "killed")
        assert result["status"] == "killed"

    def test_invalid_status_raises(self, mock_execute):
        from lina_db.server import update_agent_status

        with pytest.raises(ValueError, match="status inválido"):
            update_agent_status(self._VALID_ID, "zombified")

    def test_returns_empty_if_row_not_found(self, mock_execute):
        from lina_db.server import update_agent_status

        mock_execute.side_effect = [None, None]
        result = update_agent_status(self._VALID_ID, "running")
        assert result == {}


# ─── list_running_agents ──────────────────────────────────────────────────────


class TestListRunningAgents:
    def test_returns_active_agents(self, mock_execute):
        from lina_db.server import list_running_agents

        now = datetime.now(tz=timezone.utc)
        mock_execute.return_value = [
            {
                "id": "a" * 32,
                "role": "dev",
                "goal": "task",
                "status": "running",
                "pid": 123,
                "started_at": now,
                "elapsed_seconds": 60,
            },
        ]
        result = list_running_agents()
        assert len(result) == 1
        assert result[0]["status"] == "running"
        assert isinstance(result[0]["started_at"], str)  # serializado

    def test_returns_all_when_include_completed(self, mock_execute):
        from lina_db.server import list_running_agents

        now = datetime.now(tz=timezone.utc)
        mock_execute.return_value = [
            {
                "id": "a" * 32,
                "role": "dev",
                "goal": "task",
                "status": "completed",
                "pid": None,
                "started_at": now,
                "elapsed_seconds": 300,
            },
        ]
        result = list_running_agents(include_completed=True)
        assert len(result) == 1
        # verify that the ALL query was used (not the active view query)
        call_sql = mock_execute.call_args[0][0]
        assert "agent_sessions" in call_sql
        assert "LIMIT" in call_sql  # uses full table query with limit

    def test_returns_empty_list_when_none(self, mock_execute):
        from lina_db.server import list_running_agents

        mock_execute.return_value = None
        result = list_running_agents()
        assert result == []

    def test_active_query_uses_view(self, mock_execute):
        from lina_db.server import list_running_agents

        mock_execute.return_value = []
        list_running_agents(include_completed=False)
        call_sql = mock_execute.call_args[0][0]
        assert "agent_sessions_active" in call_sql


# ─── append_agent_event ───────────────────────────────────────────────────────


class TestAppendAgentEvent:
    _VALID_ID = "c" * 32

    def _make_row(self):
        return {"id": 1, "ts": datetime.now(tz=timezone.utc)}

    def test_inserts_event_ok(self, mock_execute):
        from lina_db.server import append_agent_event

        mock_execute.return_value = self._make_row()
        result = append_agent_event(self._VALID_ID, "heartbeat", {"msg": "alive"})
        assert result["session_id"] == self._VALID_ID
        assert result["kind"] == "heartbeat"
        assert isinstance(result["ts"], str)
        assert result["id"] == 1

    def test_all_valid_kinds(self, mock_execute):
        from lina_db.server import append_agent_event

        valid_kinds = [
            "spawn_requested",
            "process_started",
            "process_ended",
            "heartbeat",
            "tool_called",
            "error",
            "instruction_received",
            "instruction_ack",
        ]
        mock_execute.return_value = {"id": 1, "ts": datetime.now(tz=timezone.utc)}
        for kind in valid_kinds:
            result = append_agent_event(self._VALID_ID, kind)
            assert result["kind"] == kind

    def test_invalid_kind_raises(self, mock_execute):
        from lina_db.server import append_agent_event

        with pytest.raises(ValueError, match="kind inválido"):
            append_agent_event(self._VALID_ID, "deleted")

    def test_no_payload_uses_empty_dict(self, mock_execute):
        from lina_db.server import append_agent_event

        mock_execute.return_value = {"id": 2, "ts": datetime.now(tz=timezone.utc)}
        result = append_agent_event(self._VALID_ID, "heartbeat")
        assert result["kind"] == "heartbeat"

    def test_returns_none_ts_if_row_none(self, mock_execute):
        from lina_db.server import append_agent_event

        mock_execute.return_value = None
        result = append_agent_event(self._VALID_ID, "heartbeat")
        assert result["id"] is None
        assert result["ts"] is None


# ─── send_agent_command ───────────────────────────────────────────────────────


class TestSendAgentCommand:
    _VALID_ID = "d" * 32

    def _make_row(self):
        return {"id": 10, "sent_at": datetime.now(tz=timezone.utc)}

    def test_sends_pause_command(self, mock_execute):
        from lina_db.server import send_agent_command

        mock_execute.return_value = self._make_row()
        result = send_agent_command(self._VALID_ID, "pause")
        assert result["session_id"] == self._VALID_ID
        assert result["kind"] == "pause"
        assert isinstance(result["sent_at"], str)

    def test_sends_send_instruction_with_args(self, mock_execute):
        from lina_db.server import send_agent_command

        mock_execute.return_value = self._make_row()
        result = send_agent_command(self._VALID_ID, "send_instruction", {"text": "nueva tarea"})
        assert result["kind"] == "send_instruction"
        assert result["id"] == 10

    def test_all_valid_kinds(self, mock_execute):
        from lina_db.server import send_agent_command

        valid_kinds = ["pause", "resume", "kill", "send_instruction", "set_budget"]
        mock_execute.return_value = {"id": 1, "sent_at": datetime.now(tz=timezone.utc)}
        for kind in valid_kinds:
            result = send_agent_command(self._VALID_ID, kind)
            assert result["kind"] == kind

    def test_invalid_kind_raises(self, mock_execute):
        from lina_db.server import send_agent_command

        with pytest.raises(ValueError, match="kind inválido"):
            send_agent_command(self._VALID_ID, "restart")

    def test_no_args_uses_empty_dict(self, mock_execute):
        from lina_db.server import send_agent_command

        mock_execute.return_value = self._make_row()
        result = send_agent_command(self._VALID_ID, "resume")
        assert result["kind"] == "resume"

    def test_returns_none_if_row_none(self, mock_execute):
        from lina_db.server import send_agent_command

        mock_execute.return_value = None
        result = send_agent_command(self._VALID_ID, "pause")
        assert result["id"] is None
        assert result["sent_at"] is None


# ─── list_agent_events ────────────────────────────────────────────────────────


class TestListAgentEvents:
    _SID = "e" * 32

    def _make_event(self, kind: str = "heartbeat"):
        return {
            "id": 1,
            "kind": kind,
            "payload_json": {"msg": "alive"},
            "ts": datetime.now(tz=timezone.utc),
        }

    def test_returns_events_list(self, mock_execute):
        from lina_db.server import list_agent_events

        mock_execute.return_value = [self._make_event("tool_called")]
        result = list_agent_events(self._SID)
        assert len(result) == 1
        assert result[0]["kind"] == "tool_called"
        assert isinstance(result[0]["ts"], str)

    def test_empty_returns_empty_list(self, mock_execute):
        from lina_db.server import list_agent_events

        mock_execute.return_value = []
        result = list_agent_events(self._SID)
        assert result == []

    def test_none_returns_empty_list(self, mock_execute):
        from lina_db.server import list_agent_events

        mock_execute.return_value = None
        result = list_agent_events(self._SID)
        assert result == []

    def test_limit_capped_at_200(self, mock_execute):
        from lina_db.server import list_agent_events

        mock_execute.return_value = []
        list_agent_events(self._SID, limit=9999)
        call_params = mock_execute.call_args[0][1]
        assert 200 in call_params  # capped limit in SQL args tuple

    def test_limit_minimum_is_1(self, mock_execute):
        from lina_db.server import list_agent_events

        mock_execute.return_value = []
        list_agent_events(self._SID, limit=-5)
        call_params = mock_execute.call_args[0][1]
        assert 1 in call_params

    def test_empty_session_id_raises(self, mock_execute):
        from lina_db.server import list_agent_events

        with pytest.raises(ValueError, match="session_id"):
            list_agent_events("  ")

    def test_ts_none_becomes_none(self, mock_execute):
        from lina_db.server import list_agent_events

        event = self._make_event()
        event["ts"] = None
        mock_execute.return_value = [event]
        result = list_agent_events(self._SID)
        assert result[0]["ts"] is None


# ─── get_pending_instructions ─────────────────────────────────────────────────


class TestGetPendingInstructions:
    _SID = "f" * 32

    def _make_cmd(self, args: dict | None = None):
        return {
            "id": 5,
            "kind": "send_instruction",
            "args_json": args or {"text": "do something"},
            "sent_at": datetime.now(tz=timezone.utc),
        }

    def test_returns_pending_and_acks(self, mock_execute):
        from lina_db.server import get_pending_instructions

        # first call = SELECT, second = UPDATE
        mock_execute.side_effect = [[self._make_cmd()], None]
        result = get_pending_instructions(self._SID)
        assert len(result) == 1
        assert result[0]["kind"] == "send_instruction"
        assert isinstance(result[0]["sent_at"], str)
        # UPDATE was called
        assert mock_execute.call_count == 2

    def test_no_pending_returns_empty_and_skips_update(self, mock_execute):
        from lina_db.server import get_pending_instructions

        mock_execute.return_value = []
        result = get_pending_instructions(self._SID)
        assert result == []
        # only SELECT was called, no UPDATE
        assert mock_execute.call_count == 1

    def test_none_select_returns_empty_and_skips_update(self, mock_execute):
        from lina_db.server import get_pending_instructions

        mock_execute.return_value = None
        result = get_pending_instructions(self._SID)
        assert result == []
        assert mock_execute.call_count == 1

    def test_empty_session_id_raises(self, mock_execute):
        from lina_db.server import get_pending_instructions

        with pytest.raises(ValueError, match="session_id"):
            get_pending_instructions("")

    def test_sent_at_none_becomes_none(self, mock_execute):
        from lina_db.server import get_pending_instructions

        cmd = self._make_cmd()
        cmd["sent_at"] = None
        mock_execute.side_effect = [[cmd], None]
        result = get_pending_instructions(self._SID)
        assert result[0]["sent_at"] is None

    def test_multiple_instructions_all_acked(self, mock_execute):
        from lina_db.server import get_pending_instructions

        cmds = [self._make_cmd({"text": f"task {i}"}) for i in range(3)]
        for i, cmd in enumerate(cmds):
            cmd["id"] = i + 1
        mock_execute.side_effect = [cmds, None]
        result = get_pending_instructions(self._SID)
        assert len(result) == 3
        # The UPDATE call should have received list of 3 IDs
        update_params = mock_execute.call_args_list[1][0][1]
        assert len(update_params[0]) == 3

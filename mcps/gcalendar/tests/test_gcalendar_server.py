"""Unit tests — lina-gcalendar.

Testea la lógica de tools usando mocks (no hace OAuth real).
La mayoría de la lógica reside en _build_service() que se mockea completo.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reload_module(monkeypatch, tmp_path):
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{}")
    token_file = tmp_path / "token.json"
    monkeypatch.setenv("GCALENDAR_CREDENTIALS_FILE", str(creds_file))
    monkeypatch.setenv("GCALENDAR_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "8000")
    import lina_gcalendar.server as m
    importlib.reload(m)
    return m


def _mock_service(events_items=None, calendars=None):
    """Crea un mock del servicio de Google Calendar."""
    svc = MagicMock()
    if events_items is not None:
        svc.events().list().execute.return_value = {"items": events_items}
        svc.events().list.return_value = svc.events().list.return_value
    if calendars is not None:
        svc.calendarList().list().execute.return_value = {"items": calendars}
    return svc


class TestListCalendars:
    def test_returns_calendars(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = _mock_service(calendars=[
            {"id": "primary", "summary": "Mi calendario", "primary": True}
        ])
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_list_calendars()
        assert result[0]["id"] == "primary"
        assert result[0]["primary"] is True

    def test_empty_calendar_list(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = _mock_service(calendars=[])
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_list_calendars()
        assert result == []


class TestListEvents:
    def test_returns_events(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_events = [
            {"id": "ev1", "summary": "Clase de redes", "start": {"dateTime": "2025-06-15T10:00:00Z"}},
        ]
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = {"items": mock_events}
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_list_events(calendar_id="primary")
        assert len(result) == 1
        assert result[0]["summary"] == "Clase de redes"

    def test_passes_time_filters(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = {"items": []}
        with patch.object(m, "_build_service", return_value=mock_svc):
            m.gcal_list_events(
                time_min="2025-06-01T00:00:00Z",
                time_max="2025-06-30T23:59:59Z",
            )
        call_kwargs = mock_svc.events().list.call_args.kwargs
        assert call_kwargs["timeMin"] == "2025-06-01T00:00:00Z"
        assert call_kwargs["timeMax"] == "2025-06-30T23:59:59Z"


class TestCreateEvent:
    def test_inserts_event(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = MagicMock()
        mock_svc.events().insert().execute.return_value = {"id": "new_ev", "summary": "Reunión"}
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_create_event(
                summary="Reunión",
                start="2025-06-15T10:00:00-03:00",
                end="2025-06-15T11:00:00-03:00",
            )
        assert result["id"] == "new_ev"

    def test_includes_attendees_in_body(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = MagicMock()
        mock_svc.events().insert().execute.return_value = {"id": "ev2"}
        with patch.object(m, "_build_service", return_value=mock_svc):
            m.gcal_create_event(
                summary="Team meeting",
                start="2025-06-15T10:00:00Z",
                end="2025-06-15T11:00:00Z",
                attendees=["alice@example.com", "bob@example.com"],
            )
        body = mock_svc.events().insert.call_args.kwargs["body"]
        assert {"email": "alice@example.com"} in body["attendees"]


class TestDeleteEvent:
    def test_returns_deleted_flag(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = MagicMock()
        mock_svc.events().delete().execute.return_value = None
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_delete_event("primary", "ev_to_del")
        assert result["deleted"] is True
        assert result["event_id"] == "ev_to_del"


class TestUpdateEvent:
    def test_patch_only_provided_fields(self, monkeypatch):
        import lina_gcalendar.server as m
        importlib.reload(m)
        mock_svc = MagicMock()
        mock_svc.events().patch().execute.return_value = {"id": "ev1", "summary": "Updated"}
        with patch.object(m, "_build_service", return_value=mock_svc):
            result = m.gcal_update_event("primary", "ev1", summary="Updated")
        assert result["summary"] == "Updated"
        patch_body = mock_svc.events().patch.call_args.kwargs["body"]
        assert "summary" in patch_body
        assert "start" not in patch_body


class TestEnvConfig:
    def test_transport_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        import lina_gcalendar.server as m
        importlib.reload(m)
        assert m._MCP_TRANSPORT == "streamable-http"

    def test_port_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_PORT", "9099")
        import lina_gcalendar.server as m
        importlib.reload(m)
        assert m._MCP_HTTP_PORT == 9099

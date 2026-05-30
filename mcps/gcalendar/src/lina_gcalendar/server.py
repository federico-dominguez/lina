"""LINA MCP — Google Calendar v3 (OAuth2 Desktop App)

Variables de entorno:
    GCALENDAR_CREDENTIALS_FILE   ruta al credentials.json de Google Cloud Console
                                  (default: /run/gcalendar/credentials.json)
    GCALENDAR_TOKEN_FILE         ruta donde persiste el token OAuth2 tras el primer
                                  login (default: /run/gcalendar/token.json)
    MCP_TRANSPORT                stdio (default) | streamable-http
    MCP_PORT                     puerto HTTP (default 8000)

Primer uso:
    Si token.json no existe, el servidor inicia el flujo OAuth2 y espera que el
    usuario autorice en el navegador. Tras eso el token queda persistido en
    GCALENDAR_TOKEN_FILE y no vuelve a pedirse (se refresca automáticamente).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("lina-gcalendar")

# ── Config ────────────────────────────────────────────────────────────────────
_CREDENTIALS_FILE = os.environ.get(
    "GCALENDAR_CREDENTIALS_FILE", "/run/gcalendar/credentials.json"
)
_TOKEN_FILE = os.environ.get("GCALENDAR_TOKEN_FILE", "/run/gcalendar/token.json")
_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("lina-gcalendar", host="0.0.0.0", port=_MCP_HTTP_PORT)


# ── Auth ──────────────────────────────────────────────────────────────────────
def _build_service():
    """Construye el cliente de Google Calendar, refrescando token si es necesario."""
    import json

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds: Credentials | None = None

    if os.path.exists(_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(_TOKEN_FILE, _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"credentials.json no encontrado en {_CREDENTIALS_FILE}. "
                    "Montá el archivo como volumen Docker y reiniciá el servicio."
                )
            flow = InstalledAppFlow.from_client_secrets_file(_CREDENTIALS_FILE, _SCOPES)
            # En contenedor sin navegador: imprime URL y espera código
            creds = flow.run_local_server(port=0)

        # Persistir token
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        with open(_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


# ── Tools ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def gcal_list_calendars() -> list[dict]:
    """Lista todos los calendarios de la cuenta."""
    service = _build_service()
    result = service.calendarList().list().execute()
    return [
        {"id": c["id"], "summary": c.get("summary"), "primary": c.get("primary", False)}
        for c in result.get("items", [])
    ]


@mcp.tool()
def gcal_list_events(
    calendar_id: str = "primary",
    max_results: int = 20,
    time_min: str = "",
    time_max: str = "",
    query: str = "",
) -> list[dict]:
    """Lista eventos de un calendario.

    Args:
        calendar_id:  ID del calendario (default: 'primary')
        max_results:  máximo de resultados (default 20)
        time_min:     filtro desde (ISO 8601, ej: 2025-01-01T00:00:00Z)
        time_max:     filtro hasta (ISO 8601)
        query:        texto libre para buscar en título/descripción
    """
    service = _build_service()
    kwargs: dict[str, Any] = {
        "calendarId": calendar_id,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_min:
        kwargs["timeMin"] = time_min
    else:
        kwargs["timeMin"] = datetime.now(timezone.utc).isoformat()
    if time_max:
        kwargs["timeMax"] = time_max
    if query:
        kwargs["q"] = query

    result = service.events().list(**kwargs).execute()
    return result.get("items", [])


@mcp.tool()
def gcal_get_event(calendar_id: str, event_id: str) -> dict:
    """Obtiene el detalle de un evento por ID."""
    service = _build_service()
    return service.events().get(calendarId=calendar_id, eventId=event_id).execute()


@mcp.tool()
def gcal_create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
) -> dict:
    """Crea un evento en Google Calendar.

    Args:
        summary:     título del evento
        start:       inicio en ISO 8601 (ej: 2025-06-15T10:00:00-03:00)
        end:         fin en ISO 8601
        calendar_id: ID del calendario (default: 'primary')
        description: descripción del evento
        location:    lugar del evento
        attendees:   lista de emails de invitados
    """
    service = _build_service()

    def _dt_body(dt: str) -> dict:
        return {"dateTime": dt} if "T" in dt else {"date": dt}

    body: dict[str, Any] = {
        "summary": summary,
        "start": _dt_body(start),
        "end": _dt_body(end),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]

    return service.events().insert(calendarId=calendar_id, body=body).execute()


@mcp.tool()
def gcal_update_event(
    calendar_id: str,
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
) -> dict:
    """Actualiza campos de un evento existente (solo los campos provistos).

    Solo actualiza los campos que se pasen como argumento (patch semántico).
    """
    service = _build_service()
    patch: dict[str, Any] = {}
    if summary:
        patch["summary"] = summary
    if description:
        patch["description"] = description
    if location:
        patch["location"] = location

    def _dt_body(dt: str) -> dict:
        return {"dateTime": dt} if "T" in dt else {"date": dt}

    if start:
        patch["start"] = _dt_body(start)
    if end:
        patch["end"] = _dt_body(end)

    return service.events().patch(
        calendarId=calendar_id, eventId=event_id, body=patch
    ).execute()


@mcp.tool()
def gcal_delete_event(calendar_id: str, event_id: str) -> dict:
    """Elimina un evento de Google Calendar."""
    service = _build_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return {"deleted": True, "event_id": event_id}


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
        format="[lina-gcalendar] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    log.info(
        "starting lina-gcalendar (credentials=%s, transport=%s)",
        _CREDENTIALS_FILE, _MCP_TRANSPORT,
    )
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

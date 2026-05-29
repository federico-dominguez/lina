"""lina-db MCP — memoria persistente, sesiones y preferencias en PostgreSQL.

Herramientas expuestas:
    store_memory      — guarda/actualiza un recuerdo (clave-valor)
    get_memory        — recupera un recuerdo por clave
    search_memory     — búsqueda por texto en memorias activas
    summarize_session — persiste el resumen de una sesión
    get_last_sessions — recupera los N resúmenes más recientes
    store_preference  — guarda/actualiza una preferencia
    get_preferences   — lista preferencias (todas o filtradas por clave)
    get_audit_logs    — recupera los últimos N registros de auditoría
    get_daily_summary — resumen diario de uso por MCP/tool (audit.daily_summary)

Variables de entorno:
    LINA_DB_URL   URL de conexión (default: postgresql://lina:lina_dev@localhost:5432/lina)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP

log = logging.getLogger("lina-db")

# Transport config — read early because FastMCP bakes host/port at construction.
# MCP_TRANSPORT=streamable-http  enables HTTP mode (for containerised Fase 2+).
# MCP_PORT overrides the listening port in HTTP mode (default 8000).
# Without MCP_TRANSPORT the server starts in stdio mode (current / Fase 1).
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "lina-db",
    # host/port are only used when transport="streamable-http".
    host="0.0.0.0",
    port=_MCP_HTTP_PORT,
)

LINA_DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)


# ─── helpers internos ─────────────────────────────────────────────────────────


def _conn() -> psycopg2.extensions.connection:
    """Abre una conexión nueva al pool. Llamar y cerrar explícitamente."""
    return psycopg2.connect(LINA_DB_URL)


def _execute(sql: str, params: tuple = (), *, fetch: str = "none") -> Any:
    """Ejecuta una query y devuelve resultados.

    Args:
        sql:    sentencia SQL con placeholders %s
        params: tupla de parámetros
        fetch:  "none" | "one" | "all"

    Returns:
        None, dict, o list[dict] según fetch.
    """
    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
            result = dict(row) if row else None
        elif fetch == "all":
            result = [dict(r) for r in cur.fetchall()]
        else:
            result = None
        conn.commit()
        return result
    finally:
        conn.close()


def _audit(tool: str, args: dict, result_summary: str) -> None:
    """Registra una llamada en audit.tool_calls (best-effort)."""
    try:
        _execute(
            "INSERT INTO audit.tool_calls (tool, args_json, result_summary) VALUES (%s, %s, %s)",
            (tool, json.dumps(args, default=str), result_summary),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("audit log falló: %s", exc)


# ─── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def store_memory(key: str, value: str, ttl_seconds: int | None = None) -> str:
    """Almacena o actualiza un recuerdo persistente.

    Args:
        key:         identificador único del recuerdo
        value:       contenido a almacenar
        ttl_seconds: tiempo de vida en segundos (None = permanente)

    Returns:
        "ok" si es nuevo, "updated" si sobreescribió uno existente.
    """
    if not key.strip():
        raise ValueError("key no puede estar vacía")
    if not value.strip():
        raise ValueError("value no puede estar vacío")
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValueError("ttl_seconds debe ser un entero positivo")

    existing = _execute("SELECT id FROM memories WHERE key = %s", (key,), fetch="one")

    if ttl_seconds is not None:
        expires_sql = "NOW() + (%s * INTERVAL '1 second')"
        expires_param = (ttl_seconds,)
    else:
        expires_sql = "NULL"
        expires_param = ()

    if existing:
        _execute(
            f"UPDATE memories SET value = %s, updated_at = NOW(), expires_at = {expires_sql}"  # noqa: S608
            " WHERE key = %s",
            (value, *expires_param, key),
        )
        result = "updated"
    else:
        _execute(
            f"INSERT INTO memories (key, value, expires_at) VALUES (%s, %s, {expires_sql})",  # noqa: S608
            (key, value, *expires_param),
        )
        result = "ok"

    _audit("store_memory", {"key": key, "ttl_seconds": ttl_seconds}, result)
    return result


@mcp.tool()
def get_memory(key: str) -> str:
    """Recupera el valor de un recuerdo por su clave.

    Args:
        key: identificador del recuerdo

    Returns:
        El valor almacenado.

    Raises:
        KeyError: si la clave no existe o expiró.
    """
    if not key.strip():
        raise ValueError("key no puede estar vacía")

    row = _execute(
        "SELECT value FROM memories WHERE key = %s AND (expires_at IS NULL OR expires_at > NOW())",
        (key,),
        fetch="one",
    )
    if row is None:
        raise KeyError(f"recuerdo '{key}' no existe o expiró")
    return row["value"]


@mcp.tool()
def search_memory(query: str, limit: int = 20) -> list[dict]:
    """Busca recuerdos activos cuya clave o valor contenga la query.

    Args:
        query: texto a buscar (case-insensitive)
        limit: máximo de resultados (default 20, max 100)

    Returns:
        Lista de dicts con key, value, updated_at.
    """
    if not query.strip():
        raise ValueError("query no puede estar vacía")
    n = max(1, min(int(limit), 100))
    pattern = f"%{query}%"
    return (
        _execute(
            """
        SELECT key, value, updated_at
        FROM memories
        WHERE (key ILIKE %s OR value ILIKE %s)
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY updated_at DESC
        LIMIT %s
        """,
            (pattern, pattern, n),
            fetch="all",
        )
        or []
    )


@mcp.tool()
def summarize_session(session_id: str, summary: str) -> str:
    """Persiste el resumen de una sesión completada.

    Args:
        session_id: identificador de la sesión (ej. "20260529_1")
        summary:    texto del resumen

    Returns:
        "ok"
    """
    if not session_id.strip():
        raise ValueError("session_id no puede estar vacío")
    if not summary.strip():
        raise ValueError("summary no puede estar vacío")

    _execute(
        "INSERT INTO sessions (session_id, summary) VALUES (%s, %s)",
        (session_id, summary),
    )
    _audit("summarize_session", {"session_id": session_id}, "ok")
    return "ok"


@mcp.tool()
def get_last_sessions(n: int = 5) -> list[dict]:
    """Recupera los N resúmenes de sesión más recientes.

    Args:
        n: cantidad de sesiones (default 5, max 50)

    Returns:
        Lista de dicts con session_id, summary, created_at.
    """
    limit = max(1, min(int(n), 50))
    return (
        _execute(
            "SELECT session_id, summary, created_at"
            " FROM sessions ORDER BY created_at DESC LIMIT %s",
            (limit,),
            fetch="all",
        )
        or []
    )


@mcp.tool()
def store_preference(key: str, value: str) -> str:
    """Guarda o actualiza una preferencia del usuario.

    Args:
        key:   nombre de la preferencia
        value: valor de la preferencia

    Returns:
        "ok" si es nueva, "updated" si sobreescribió una existente.
    """
    if not key.strip():
        raise ValueError("key no puede estar vacía")

    existing = _execute("SELECT id FROM preferences WHERE key = %s", (key,), fetch="one")
    if existing:
        _execute(
            "UPDATE preferences SET value = %s, updated_at = NOW() WHERE key = %s",
            (value, key),
        )
        result = "updated"
    else:
        _execute(
            "INSERT INTO preferences (key, value) VALUES (%s, %s)",
            (key, value),
        )
        result = "ok"
    _audit("store_preference", {"key": key}, result)
    return result


@mcp.tool()
def get_preferences(key: str | None = None) -> list[dict]:
    """Lista preferencias almacenadas.

    Args:
        key: si se especifica, filtra por esa clave exacta

    Returns:
        Lista de dicts con key, value, updated_at.
    """
    if key:
        return (
            _execute(
                "SELECT key, value, updated_at FROM preferences WHERE key = %s",
                (key,),
                fetch="all",
            )
            or []
        )
    return (
        _execute(
            "SELECT key, value, updated_at FROM preferences ORDER BY key",
            fetch="all",
        )
        or []
    )


@mcp.tool()
def get_audit_logs(limit: int = 50) -> list[dict]:
    """Recupera los N registros de auditoría más recientes de audit.tool_calls.

    Args:
        limit: cantidad de registros (default 50, max 500)

    Returns:
        Lista de dicts con mcp, tool, args_json, result_summary, created_at.
    """
    n = max(1, min(int(limit), 500))
    return (
        _execute(
            "SELECT mcp, tool, args_json, result_summary, created_at"
            " FROM audit.tool_calls ORDER BY created_at DESC LIMIT %s",
            (n,),
            fetch="all",
        )
        or []
    )


@mcp.tool()
def get_daily_summary(days: int = 7) -> list[dict]:
    """Resumen diario de uso por MCP y tool (vista audit.daily_summary).

    Args:
        days: cantidad de días hacia atrás a incluir (default 7, max 90)

    Returns:
        Lista de dicts con day, mcp, tool, calls, avg_ms, max_ms.
    """
    n = max(1, min(int(days), 90))
    return (
        _execute(
            "SELECT day, mcp, tool, calls, avg_ms, max_ms"
            " FROM audit.daily_summary"
            " WHERE day >= NOW() - (%s * INTERVAL '1 day')",
            (n,),
            fetch="all",
        )
        or []
    )


# ─── entrypoint ───────────────────────────────────────────────────────────────


def main() -> None:
    log.info("lina-db starting (db=%s transport=%s)", LINA_DB_URL.split("@")[-1], _MCP_TRANSPORT)
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

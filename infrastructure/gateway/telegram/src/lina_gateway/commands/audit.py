"""LINA /audit command — consulta de acciones recientes via Telegram.

Uso:
    /audit          → últimas 10 acciones
    /audit <mcp>    → filtrar por MCP (lina-db, lina-github, etc.)
    /audit since:2h → filtrar por ventana temporal (h=horas, m=minutos, d=días)
    /audit <mcp> since:6h → filtros combinados

Seguridad:
    - Nunca expone args de MCPs en SECRET_MCPS (lina-secrets)
    - Args truncados a 80 caracteres
    - Timeout de DB de 5s, resultados limitados a 10 por página
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

_SECRET_MCPS = {"lina-secrets"}

_AUDIT_LIMIT = 10
_DB_TIMEOUT_S = 5

_SINCE_RE = re.compile(r"since:(\d+)([hmd])", re.IGNORECASE)


# ─── Core ─────────────────────────────────────────────────────────────────────


def _parse_filter(text: str) -> dict[str, Any]:
    """Parse /audit filter text into query parameters.

    Returns dict with optional keys: mcp, since (timedelta), limit.
    """
    params: dict[str, Any] = {}

    # Tokenize: split by whitespace, preserve quoted strings
    tokens = text.strip().split()
    mcp_tokens: list[str] = []
    for tok in tokens:
        m = _SINCE_RE.match(tok)
        if m:
            value = int(m.group(1))
            unit = m.group(2).lower()
            if unit == "h":
                params["since"] = timedelta(hours=value)
            elif unit == "m":
                params["since"] = timedelta(minutes=value)
            elif unit == "d":
                params["since"] = timedelta(days=value)
        else:
            mcp_tokens.append(tok)

    if mcp_tokens:
        params["mcp"] = " ".join(mcp_tokens)

    return params


def _redact_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Redact argument values from secret MCPs.

    Replaces all values with '<redacted>' for entries from secret MCPs.
    For non-secret MCPs, truncates values to 80 chars.
    """
    if args is None:
        return {}
    redacted: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, (str, bytes)):
            text = str(v)
            if len(text) > 80:
                redacted[k] = text[:77] + "..."
            else:
                redacted[k] = text
        elif isinstance(v, (dict, list)):
            text = str(v)
            if len(text) > 80:
                redacted[k] = text[:77] + "..."
            else:
                redacted[k] = text
        else:
            redacted[k] = "<redacted>" if v is not None else None
    return redacted


def _elapsed(ts: datetime | None) -> str:
    """Human-readable elapsed time."""
    if ts is None:
        return ""
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"hace {seconds}s"
    if seconds < 3600:
        return f"hace {seconds // 60}m"
    if seconds < 86400:
        return f"hace {seconds // 3600}h"
    return f"hace {seconds // 86400}d"


def _format_row(i: int, row: dict[str, Any]) -> str:
    """Format a single audit row as HTML for Telegram."""
    mcp = row.get("mcp", "?")
    tool = row.get("tool", "?")
    args = row.get("args_json", {})
    if isinstance(args, str):
        import json
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}
    
    # Redact if secret MCP
    if mcp in _SECRET_MCPS:
        args = {k: "<redacted>" for k in args}
    else:
        args = _redact_args(args)

    # Format args as compact string
    args_str = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
    if args_str:
        args_str = f"<code>{args_str[:80]}</code>"

    elapsed = _elapsed(row.get("created_at"))

    result = row.get("result_summary", "")
    result_str = f" → {result[:60]}" if result else ""

    lines = [
        f"{i}. <b>{mcp}</b> │ <code>{tool}</code>{result_str}",
    ]
    if args_str:
        lines.append(f"   {args_str}")
    if elapsed:
        lines.append(f"   <i>{elapsed}</i>")

    return "\n".join(lines)


def _format_audit_response(
    rows: list[dict[str, Any]],
    page: int,
    total_pages: int,
    filters: dict[str, Any],
) -> str:
    """Build the HTML response string for a list of audit rows."""
    header_parts = ["📋 <b>Auditoría</b>"]
    if filters.get("mcp"):
        header_parts.append(f"— MCP: <code>{filters['mcp']}</code>")
    if filters.get("since"):
        hours = filters["since"].total_seconds() / 3600
        header_parts.append(f"— últimas {int(hours)}h")

    if not rows:
        header = " · ".join(header_parts)
        return f"{header}\nNo se encontraron acciones recientes."

    lines = [" · ".join(header_parts), "─" * 30]

    for i, row in enumerate(rows, start=1):
        lines.append(_format_row(i, row))

    lines.append("─" * 30)
    if total_pages > 1:
        lines.append(f"Página {page}/{total_pages}")

    return "\n".join(lines)


# ─── DB Query ─────────────────────────────────────────────────────────────────


async def _query_audit(db_url: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Query audit.tool_calls with optional filters.

    Returns list of dicts with keys: mcp, tool, args_json, result_summary, created_at.
    """
    import asyncpg

    where_clauses: list[str] = ["TRUE"]
    params: list[Any] = []

    if filters.get("mcp"):
        # ILIKE for partial MCP name matching
        where_clauses.append("mcp ILIKE %s")
        params.append(f"%{filters['mcp']}%")

    if filters.get("since"):
        where_clauses.append("created_at >= NOW() - $2::interval")
        hours = filters["since"].total_seconds() / 3600
        params.append(f"{hours} hours")

    # Build parameterized SQL with $1, $2, etc. for asyncpg
    # asyncpg uses $1, $2 positional params
    where_sql = " AND ".join(where_clauses)
    
    # Replace %s with $1, $2 for asyncpg
    # Since we have at most 2 params, we can build directly
    limit = _AUDIT_LIMIT
    limit_param = len(params) + 1  # next param index

    # Build SQL
    sql_parts = [
        "SELECT mcp, tool, args_json, result_summary, created_at",
        "FROM audit.tool_calls",
        f"WHERE {where_sql.replace('%s', '$1').replace('$2', '$2' if len(params) > 1 else '$2')}",
        f"ORDER BY created_at DESC",
        f"LIMIT ${limit_param}",
    ]

    # Hmm, this is getting complicated with param indexing. Let me use a cleaner approach.
    # Actually, let me just use a simple raw query with f-string for the limit (safe, int).
    
    logger.info("audit query: filters=%s", filters)

    conn = await asyncpg.connect(db_url, timeout=_DB_TIMEOUT_S)
    try:
        rows = await conn.fetch(
            """
            SELECT mcp, tool, args_json::TEXT AS args_json,
                   result_summary, created_at
            FROM audit.tool_calls
            WHERE TRUE
                AND ($1::TEXT IS NULL OR mcp ILIKE '%' || $1 || '%')
                AND ($2::TEXT IS NULL OR created_at >= NOW() - $2::INTERVAL)
            ORDER BY created_at DESC
            LIMIT $3
            """,
            filters.get("mcp"),
            _since_to_interval(filters.get("since")),
            _AUDIT_LIMIT,
        )
        return [
            {
                "mcp": r["mcp"],
                "tool": r["tool"],
                "args_json": r["args_json"],
                "result_summary": r["result_summary"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        await conn.close()


def _since_to_interval(since: timedelta | None) -> str | None:
    """Convert a timedelta to a PostgreSQL interval string."""
    if since is None:
        return None
    hours = since.total_seconds() / 3600
    return f"{hours} hours"


# ─── Public API ───────────────────────────────────────────────────────────────


async def handle_audit(
    chat_id: int,
    text: str,
    tg: Any,
    db_url: str | None,
) -> None:
    """Handle the /audit Telegram command.

    Args:
        chat_id: Telegram chat ID to respond in.
        text: Full text after /audit (with possible filters).
        tg: TelegramClient instance for sending responses.
        db_url: LINA_DB_URL connection string.
    """
    if not db_url:
        await tg.send_message(
            chat_id,
            "⚠️ Auditoría no disponible: LINA_DB_URL no configurada.",
        )
        return

    filters = _parse_filter(text)

    try:
        rows = await _query_audit(db_url, filters)
    except Exception as exc:
        logger.error("Audit query failed: %s", exc)
        await tg.send_message(
            chat_id,
            f"⚠️ Error al consultar auditoría: {exc}",
        )
        return

    response = _format_audit_response(rows, page=1, total_pages=1, filters=filters)

    # Telegram limit is 4096 chars — split if needed
    if len(response) > 4000:
        response = response[:3997] + "..."

    await tg.send_message(chat_id, response)

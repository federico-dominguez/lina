"""lina-db MCP — memoria persistente, sesiones y preferencias en PostgreSQL.

Herramientas expuestas:
    store_memory             — guarda/actualiza un recuerdo (clave-valor)
    get_memory               — recupera un recuerdo por clave
    search_memory            — búsqueda por texto en memorias activas
    summarize_session        — persiste el resumen de una sesión (legacy)
    summarize_session_smart  — persiste resumen estructurado con topics/facts/pending
    get_session_summary      — recupera el resumen estructurado de una sesión
    get_last_sessions        — recupera los N resúmenes más recientes
    store_preference         — guarda/actualiza una preferencia
    get_preferences          — lista preferencias (todas o filtradas por clave)
    get_audit_logs           — recupera los últimos N registros de auditoría
    get_daily_summary        — resumen diario de uso por MCP/tool (audit.daily_summary)
    get_session_cost         — costo estimado acumulado de una sesión (issue #61)
    get_daily_cost           — costo estimado por día (últimos N días) (issue #61)
    get_deepseek_balance          — saldo actual de la cuenta DeepSeek via API oficial (issue #61)
    get_deepseek_user_summary     — balance + gasto mensual exacto desde platform.deepseek.com
    get_deepseek_monthly_usage    — tokens por modelo y tipo desde platform.deepseek.com
    get_deepseek_monthly_cost     — costo USD por modelo desde platform.deepseek.com (= dashboard)
    get_last_traces          — últimos N bloques <think> de LINA (issue #62)
    search_traces            — búsqueda full-text en reasoning traces de LINA (issue #62)
    store_semantic_memory    — guarda un recuerdo con embedding vectorial (issue #63)
    search_semantic_memory   — búsqueda semántica por similitud de coseno (issue #63)
    create_agent_session     — registra nueva sesión de sub-agente en DB (issue #83)
    update_agent_status      — actualiza estado/pid de un sub-agente (issue #83)
    list_running_agents      — lista sub-agentes activos o todos (issue #83)
    append_agent_event       — añade evento al log inmutable de un agente (issue #83)
    send_agent_command       — envía comando al buzón de un sub-agente (issue #83)
    list_agent_events        — devuelve el log de eventos de un sub-agente (issue #88)
    get_pending_instructions — lee y ackea instrucciones pendientes (issue #90)

Variables de entorno:
    LINA_DB_URL              URL de conexión (default: postgresql://lina:lina_dev@localhost:5432/lina)
    DEEPSEEK_API_KEY         API key de DeepSeek para get_deepseek_balance (opcional)
    DEEPSEEK_PLATFORM_TOKEN  Token de sesión de platform.deepseek.com (opcional)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from pgvector.psycopg2 import register_vector

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
    conn = psycopg2.connect(LINA_DB_URL)
    register_vector(conn)
    return conn


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


def _embed(text: str) -> list[float]:
    """Genera un embedding vectorial usando la API de DeepSeek.

    Usa el modelo text-embedding-3-small (1536 dimensiones) via
    https://api.deepseek.com/v1/embeddings. Requiere DEEPSEEK_API_KEY.

    Args:
        text: texto a embeber (max ~8000 tokens).

    Returns:
        Lista de floats (1536 dimensiones).
    """
    import json as _json
    import urllib.request as _ur

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY no configurada — no se puede generar embedding")

    payload = _json.dumps(
        {
            "model": "text-embedding-3-small",
            "input": text,
        }
    ).encode("utf-8")

    req = _ur.Request(
        "https://api.deepseek.com/v1/embeddings",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with _ur.urlopen(req, timeout=30) as resp:
        data = _json.loads(resp.read().decode())

    embedding = data["data"][0]["embedding"]
    return embedding


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


@mcp.tool()
def summarize_session_smart(
    session_id: str,
    raw_summary: str,
    topics: list[str] | None = None,
    facts: list[str] | None = None,
    pending: list[str] | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> str:
    """Guarda un resumen estructurado de la sesión actual para uso en futuras sesiones.

    Este resumen es usado por el gateway al iniciar una sesión nueva para inyectar
    contexto compacto (~200 tokens) en lugar de mensajes crudos (~4000 tokens).
    Llamar al final de cada sesión (o via recipe session-end.yaml).

    Args:
        session_id:  ID de la sesión (ej: "telegram-123456789").
        raw_summary: Resumen en prosa de la sesión (qué se hizo, decisiones, estado).
        topics:      Lista de temas principales (ej: ["Moodle M2-R7", "deploy gateway"]).
        facts:       Datos puntuales para recordar (ej: ["número favorito = 42"]).
        pending:     Tareas no completadas (ej: ["revisar PR #60 mañana"]).
        tokens_in:   Tokens de entrada consumidos en la sesión (opcional).
        tokens_out:  Tokens de salida generados en la sesión (opcional).

    Returns:
        "created" si es la primera vez, "updated" si ya existía un resumen para esa sesión.
    """
    if not session_id.strip():
        raise ValueError("session_id no puede estar vacío")
    if not raw_summary.strip():
        raise ValueError("raw_summary no puede estar vacío")

    topics_val = topics or []
    facts_val = facts or []
    pending_val = pending or []

    existing = _execute(
        "SELECT id FROM session_summaries WHERE session_id = %s",
        (session_id,),
        fetch="one",
    )

    if existing:
        _execute(
            """
            UPDATE session_summaries
            SET raw_summary = %s,
                topics      = %s,
                facts       = %s,
                pending     = %s,
                tokens_in   = %s,
                tokens_out  = %s,
                updated_at  = NOW()
            WHERE session_id = %s
            """,
            (
                raw_summary,
                topics_val,
                facts_val,
                pending_val,
                tokens_in,
                tokens_out,
                session_id,
            ),
        )
        result = "updated"
    else:
        _execute(
            """
            INSERT INTO session_summaries
                (session_id, raw_summary, topics, facts, pending, tokens_in, tokens_out)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                raw_summary,
                topics_val,
                facts_val,
                pending_val,
                tokens_in,
                tokens_out,
            ),
        )
        result = "created"

    _audit(
        "summarize_session_smart",
        {"session_id": session_id, "topics": topics_val, "facts_count": len(facts_val)},
        result,
    )
    return result


@mcp.tool()
def get_session_summary(session_id: str) -> dict | None:
    """Recupera el resumen estructurado más reciente de una sesión.

    Args:
        session_id: ID de la sesión (ej: "telegram-123456789").

    Returns:
        Dict con raw_summary, topics, facts, pending, updated_at — o None si no existe.
    """
    row = _execute(
        """
        SELECT raw_summary, topics, facts, pending, tokens_in, tokens_out, updated_at
        FROM session_summaries
        WHERE session_id = %s
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id,),
        fetch="one",
    )
    return dict(row) if row else None


# ─── Token / cost metering (issue #61) ───────────────────────────────────────


@mcp.tool()
def get_session_cost(session_id: str) -> dict:
    """Retorna el costo estimado acumulado de una sesión de Telegram.

    Suma todos los turnos registrados en ``token_usage`` para ``session_id``.

    Args:
        session_id: ID de la sesión (ej: "telegram-123456789").

    Returns:
        Dict con session_id, turns, prompt_tokens_est, completion_tokens_est,
        total_tokens_est, cost_usd_est (suma).  Si no hay datos, devuelve ceros.
    """
    row = _execute(
        """
        SELECT
            COUNT(*)                        AS turns,
            COALESCE(SUM(prompt_tokens_est), 0)     AS prompt_tokens_est,
            COALESCE(SUM(completion_tokens_est), 0) AS completion_tokens_est,
            COALESCE(SUM(prompt_tokens_est + completion_tokens_est), 0) AS total_tokens_est,
            COALESCE(SUM(cost_usd_est), 0)          AS cost_usd_est
        FROM token_usage
        WHERE session_id = %s
        """,
        (session_id,),
        fetch="one",
    )
    result = dict(row) if row else {}
    result["session_id"] = session_id
    return result


@mcp.tool()
def get_daily_cost(days: int = 7) -> list[dict]:
    """Resumen de costo estimado por día (últimos N días).

    Agrupa todos los turnos de ``token_usage`` por fecha UTC y retorna
    la suma de tokens y costo por día.

    Args:
        days: Número de días hacia atrás (default 7, max 90).

    Returns:
        Lista de dicts con day, turns, prompt_tokens_est, completion_tokens_est,
        total_tokens_est, cost_usd_est, ordenada más-reciente primero.
    """
    n = max(1, min(int(days), 90))
    return (
        _execute(
            """
            SELECT
                DATE(created_at AT TIME ZONE 'UTC') AS day,
                COUNT(*)                             AS turns,
                SUM(prompt_tokens_est)               AS prompt_tokens_est,
                SUM(completion_tokens_est)           AS completion_tokens_est,
                SUM(prompt_tokens_est + completion_tokens_est) AS total_tokens_est,
                SUM(cost_usd_est)                    AS cost_usd_est
            FROM token_usage
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY DATE(created_at AT TIME ZONE 'UTC')
            ORDER BY day DESC
            """,
            (n,),
            fetch="all",
        )
        or []
    )


@mcp.tool()
def get_deepseek_balance() -> dict:
    """Consulta el saldo actual de la cuenta DeepSeek via su API oficial.

    Llama a ``GET https://api.deepseek.com/user/balance`` usando la API key
    configurada en el entorno (variable ``DEEPSEEK_API_KEY``).

    Returns:
        Dict con is_available y balance_infos (lista de objetos con
        currency, total_balance, granted_balance, topped_up_balance).
        Si la API key no está disponible o la llamada falla, retorna
        un dict con error explicativo.

    Note:
        DeepSeek no expone historial de uso por sesión — solo el saldo restante.
        Usar ``get_session_cost`` / ``get_daily_cost`` para desglose local.
    """
    import urllib.request

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"error": "DEEPSEEK_API_KEY no configurada en el entorno del MCP"}

    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        import json as _json

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
        _audit("get_deepseek_balance", {}, f"is_available={data.get('is_available')}")
        return data
    except Exception as exc:
        return {"error": str(exc)}


def _platform_request(path: str, platform_token: str) -> dict:
    """Make an authenticated GET to platform.deepseek.com and return parsed JSON."""
    import json as _json
    import urllib.request as _ur

    req = _ur.Request(
        f"https://platform.deepseek.com{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {platform_token}",
        },
        method="GET",
    )
    with _ur.urlopen(req, timeout=15) as resp:
        return _json.loads(resp.read().decode())


@mcp.tool()
def get_deepseek_user_summary() -> dict:
    """Consulta el resumen de cuenta DeepSeek Platform: balance, gasto mensual y tokens usados.

    Llama a ``GET platform.deepseek.com/api/v0/users/get_user_summary`` con el
    token de sesión de la plataforma (``DEEPSEEK_PLATFORM_TOKEN``).

    Los números devueltos son **idénticos** a los del dashboard de platform.deepseek.com.

    Returns:
        Dict con:
        - balance_usd: saldo de cuenta en USD (ej. "9.95")
        - monthly_cost_usd: gasto del mes actual en USD (ej. "5.05")
        - monthly_token_usage: tokens usados en el mes
        - token_estimation: tokens estimados restantes con el balance actual
        Si el token no está configurado o expiró, retorna un dict con error.
    """
    platform_token = os.environ.get("DEEPSEEK_PLATFORM_TOKEN", "")
    if not platform_token:
        return {
            "error": "DEEPSEEK_PLATFORM_TOKEN no configurado. "
            "Obtenerlo de platform.deepseek.com (sesión de navegador)."
        }

    try:
        data = _platform_request("/api/v0/users/get_user_summary", platform_token)
        if data.get("code") != 0:
            return {"error": f"API error: {data.get('msg', 'unknown')} (code={data.get('code')})"}

        biz = data["data"]["biz_data"]
        wallets = biz.get("normal_wallets", [])
        usd_wallet = next((w for w in wallets if w.get("currency") == "USD"), {})
        monthly_costs = biz.get("monthly_costs", [])
        usd_cost = next((c for c in monthly_costs if c.get("currency") == "USD"), {})

        result = {
            "balance_usd": usd_wallet.get("balance", "0"),
            "monthly_cost_usd": usd_cost.get("amount", "0"),
            "monthly_token_usage": biz.get("monthly_token_usage", "0"),
            "token_estimation": usd_wallet.get("token_estimation", "0"),
        }
        _audit(
            "get_deepseek_user_summary",
            {},
            f"balance=${result['balance_usd'][:6]} monthly=${result['monthly_cost_usd'][:6]}",
        )
        return result
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_deepseek_monthly_usage(year: int, month: int) -> dict:
    """Consulta tokens usados por modelo en un mes, desde la API real de DeepSeek Platform.

    Llama a ``GET platform.deepseek.com/api/v0/usage/amount?month={month}&year={year}``.
    Desglose por modelo (deepseek-v4-flash, deepseek-v4-pro) y tipo de token
    (PROMPT_CACHE_HIT_TOKEN, PROMPT_CACHE_MISS_TOKEN, RESPONSE_TOKEN, REQUEST).

    Args:
        year:  Año (ej. 2026)
        month: Mes 1-12 (ej. 5 = mayo)

    Returns:
        Dict con lista por modelo. Ejemplo:
        {
          "deepseek-v4-flash": {
            "cache_hit_tokens": 178058496,
            "cache_miss_tokens": 15741513,
            "output_tokens": 1067772,
            "requests": 3648
          }, ...
        }
    """
    platform_token = os.environ.get("DEEPSEEK_PLATFORM_TOKEN", "")
    if not platform_token:
        return {"error": "DEEPSEEK_PLATFORM_TOKEN no configurado."}

    try:
        data = _platform_request(f"/api/v0/usage/amount?month={month}&year={year}", platform_token)
        if data.get("code") != 0:
            return {"error": f"API error: {data.get('msg')} (code={data.get('code')})"}

        models_raw = data["data"]["biz_data"].get("total", [])
        result: dict[str, dict] = {}
        for model_entry in models_raw:
            model = model_entry["model"]
            usage_map: dict[str, int] = {}
            for u in model_entry.get("usage", []):
                usage_map[u["type"]] = int(u["amount"])
            result[model] = {
                "cache_hit_tokens": usage_map.get("PROMPT_CACHE_HIT_TOKEN", 0),
                "cache_miss_tokens": usage_map.get("PROMPT_CACHE_MISS_TOKEN", 0),
                "output_tokens": usage_map.get("RESPONSE_TOKEN", 0),
                "requests": usage_map.get("REQUEST", 0),
            }
        _audit(
            "get_deepseek_monthly_usage",
            {"year": year, "month": month},
            f"models={list(result.keys())}",
        )
        return {"year": year, "month": month, "usage": result}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_deepseek_monthly_cost(year: int, month: int) -> dict:
    """Consulta el costo real en USD por modelo y tipo de token para un mes dado.

    Llama a ``GET platform.deepseek.com/api/v0/usage/cost?month={month}&year={year}``.
    Los números son **idénticos** a los del dashboard de DeepSeek Platform.

    Args:
        year:  Año (ej. 2026)
        month: Mes 1-12 (ej. 5 = mayo)

    Returns:
        Dict con costo total y desglose por modelo. Ejemplo:
        {
          "total_usd": 5.0460,
          "by_model": {
            "deepseek-v4-flash": {
              "cache_hit_cost_usd": 0.4986,
              "cache_miss_cost_usd": 2.2039,
              "output_cost_usd": 0.2990,
              "total_usd": 3.0015
            }, ...
          }
        }
    """
    platform_token = os.environ.get("DEEPSEEK_PLATFORM_TOKEN", "")
    if not platform_token:
        return {"error": "DEEPSEEK_PLATFORM_TOKEN no configurado."}

    try:
        data = _platform_request(f"/api/v0/usage/cost?month={month}&year={year}", platform_token)
        if data.get("code") != 0:
            return {"error": f"API error: {data.get('msg')} (code={data.get('code')})"}

        # API returns a list; first element has "total" key
        biz_data = data["data"]["biz_data"]
        entries = biz_data if isinstance(biz_data, list) else [biz_data]
        total_entry = entries[0] if entries else {}
        models_raw = total_entry.get("total", [])

        by_model: dict[str, dict] = {}
        grand_total = 0.0
        for model_entry in models_raw:
            model = model_entry["model"]
            cost_map: dict[str, float] = {}
            for u in model_entry.get("usage", []):
                cost_map[u["type"]] = float(u["amount"])
            cache_hit = cost_map.get("PROMPT_CACHE_HIT_TOKEN", 0.0)
            cache_miss = cost_map.get("PROMPT_CACHE_MISS_TOKEN", 0.0)
            output = cost_map.get("RESPONSE_TOKEN", 0.0)
            model_total = cache_hit + cache_miss + output
            grand_total += model_total
            by_model[model] = {
                "cache_hit_cost_usd": round(cache_hit, 8),
                "cache_miss_cost_usd": round(cache_miss, 8),
                "output_cost_usd": round(output, 8),
                "total_usd": round(model_total, 8),
            }
        _audit(
            "get_deepseek_monthly_cost",
            {"year": year, "month": month},
            f"total=${round(grand_total, 4)}",
        )
        return {
            "year": year,
            "month": month,
            "total_usd": round(grand_total, 8),
            "by_model": by_model,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ─── Reasoning trace tools (issue #62) ────────────────────────────────────────


@mcp.tool()
def get_last_traces(session_id: str = "", limit: int = 5) -> list[dict]:
    """Recupera los últimos N bloques <think> de LINA para auto-análisis.

    Permite a LINA introspeccionar su propio razonamiento pasado. Si se proporciona
    ``session_id``, filtra por esa sesión; si está vacío devuelve las más recientes
    sin importar la sesión.

    Args:
        session_id: ID de sesión (e.g. 'telegram-123456789'). Vacío = todas las sesiones.
        limit:      Máximo de trazas a devolver (default 5, máximo 20).

    Returns:
        Lista de dicts con: id, session_id, turn_number, thinking_text,
        prompt_hash, model, created_at.
    """
    limit = min(max(1, limit), 20)
    try:
        with _conn() as conn, conn.cursor() as cur:
            if session_id:
                cur.execute(
                    """
                    SELECT id, session_id, turn_number, thinking_text,
                           prompt_hash, model,
                           created_at AT TIME ZONE 'UTC' AS created_at
                    FROM reasoning_traces
                    WHERE session_id = %s
                    ORDER BY turn_number DESC
                    LIMIT %s
                    """,
                    (session_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, session_id, turn_number, thinking_text,
                           prompt_hash, model,
                           created_at AT TIME ZONE 'UTC' AS created_at
                    FROM reasoning_traces
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            result = []
            for row in rows:
                item = dict(zip(cols, row))
                item["created_at"] = str(item["created_at"])
                # Truncate very long traces for readability (first 1000 chars)
                if item.get("thinking_text") and len(item["thinking_text"]) > 1000:
                    item["thinking_text"] = item["thinking_text"][:1000] + "…[truncado]"
                result.append(item)
        _audit("get_last_traces", {"session_id": session_id, "limit": limit}, f"rows={len(result)}")
        return result
    except Exception as exc:
        return [{"error": str(exc)}]


@mcp.tool()
def search_traces(query: str, limit: int = 10) -> list[dict]:
    """Búsqueda full-text en el historial de razonamiento de LINA.

    Permite a LINA buscar en todos sus trazos de razonamiento por palabras clave,
    para responder preguntas como '¿cuándo pensé en el problema X?' o
    '¿qué razoné sobre Moodle?'.

    Args:
        query: Texto a buscar (palabras clave, frases). Se usa pg tsvector en español.
        limit: Máximo de resultados (default 10, máximo 20).

    Returns:
        Lista de dicts con id, session_id, turn_number, thinking_text (truncado a 500 chars),
        model, created_at y rank (relevancia).
    """
    limit = min(max(1, limit), 20)
    if not query.strip():
        return [{"error": "query no puede estar vacío"}]
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, session_id, turn_number,
                       left(thinking_text, 500) AS thinking_text,
                       model,
                       created_at AT TIME ZONE 'UTC' AS created_at,
                       ts_rank(
                           to_tsvector('spanish', thinking_text),
                           plainto_tsquery('spanish', %s)
                       ) AS rank
                FROM reasoning_traces
                WHERE to_tsvector('spanish', thinking_text) @@ plainto_tsquery('spanish', %s)
                ORDER BY rank DESC, created_at DESC
                LIMIT %s
                """,
                (query, query, limit),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            result = []
            for row in rows:
                item = dict(zip(cols, row))
                item["created_at"] = str(item["created_at"])
                item["rank"] = round(float(item["rank"]), 4)
                result.append(item)
        _audit("search_traces", {"query": query[:50], "limit": limit}, f"hits={len(result)}")
        return result
    except Exception as exc:
        return [{"error": str(exc)}]


# ─── Semantic memory tools (issue #63) ────────────────────────────────────────


@mcp.tool()
def store_semantic_memory(
    key: str,
    value: str,
    ttl_seconds: int | None = None,
    auto_embed: bool = True,
    embedding: list[float] | None = None,
) -> str:
    """Guarda o actualiza un recuerdo persistente con embedding vectorial.

    Si ``auto_embed=True`` (default), genera automáticamente el embedding
    llamando a DeepSeek Embeddings API (modelo ``text-embedding-3-small``,
    1536 dimensiones). Alternativamente, se puede pasar `embedding` explícito.

    Args:
        key:         identificador único del recuerdo
        value:       contenido textual a almacenar
        ttl_seconds: tiempo de vida en segundos (None = permanente)
        auto_embed:  si es True, genera embedding via API (default)
        embedding:   vector explícito (1536 floats) — ignora auto_embed si se provee

    Returns:
        "ok" si es nuevo, "updated" si sobreescribió uno existente.
    """
    if not key.strip():
        raise ValueError("key no puede estar vacía")
    if not value.strip():
        raise ValueError("value no puede estar vacío")
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValueError("ttl_seconds debe ser un entero positivo")

    # Resolver embedding
    if embedding:
        emb = embedding
    elif auto_embed:
        emb = _embed(value)
    else:
        emb = None

    if ttl_seconds is not None:
        expires_sql = "NOW() + (%s * INTERVAL '1 second')"
        expires_param = (ttl_seconds,)
    else:
        expires_sql = "NULL"
        expires_param = ()

    existing = _execute("SELECT id FROM memories WHERE key = %s", (key,), fetch="one")

    if existing:
        if emb:
            _execute(
                f"UPDATE memories SET value = %s, updated_at = NOW(), expires_at = {expires_sql},"  # noqa: S608
                " embedding = %s WHERE key = %s",
                (value, *expires_param, emb, key),
            )
        else:
            _execute(
                f"UPDATE memories SET value = %s, updated_at = NOW(), expires_at = {expires_sql}"  # noqa: S608
                " WHERE key = %s",
                (value, *expires_param, key),
            )
        result = "updated"
    else:
        if emb:
            _execute(
                f"INSERT INTO memories (key, value, expires_at, embedding)"  # noqa: S608
                f" VALUES (%s, %s, {expires_sql}, %s)",
                (key, value, *expires_param, emb),
            )
        else:
            _execute(
                f"INSERT INTO memories (key, value, expires_at) VALUES (%s, %s, {expires_sql})",  # noqa: S608
                (key, value, *expires_param),
            )
        result = "ok"

    _audit("store_semantic_memory", {"key": key, "auto_embed": auto_embed}, result)
    return result


@mcp.tool()
def search_semantic_memory(
    query: str,
    limit: int = 10,
    threshold: float = 0.5,
) -> list[dict]:
    """Busca recuerdos semánticamente similares a la query.

    Genera embedding de la query via DeepSeek Embeddings API y realiza
    búsqueda ANN (IVFFlat) por cosine similarity en la tabla memories.

    Args:
        query:     texto a buscar semánticamente
        limit:     máximo de resultados (default 10, max 50)
        threshold: similitud mínima de coseno (0 a 1, default 0.5).
                   Mayor = más restrictivo.

    Returns:
        Lista de dicts con key, value, similarity (cosine distance → similarity),
        updated_at. Ordenada por similitud descendente.
    """
    if not query.strip():
        raise ValueError("query no puede estar vacía")
    n = max(1, min(int(limit), 50))
    t = max(0.0, min(float(threshold), 1.0))

    emb = _embed(query)
    emb_str = "[" + ",".join(str(v) for v in emb) + "]"

    # Cosine distance → similarity: 1 - distance
    rows = _execute(
        """
        SELECT key, value,
               1 - (embedding <=> %s::vector) AS similarity,
               updated_at
        FROM memories
        WHERE embedding IS NOT NULL
          AND (expires_at IS NULL OR expires_at > NOW())
          AND 1 - (embedding <=> %s::vector) >= %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (emb_str, emb_str, t, emb_str, n),
        fetch="all",
    )
    if rows:
        for r in rows:
            r["similarity"] = round(float(r["similarity"]), 4)
    _audit(
        "search_semantic_memory",
        {"query": query[:100], "threshold": t},
        f"hits={len(rows) if rows else 0}",
    )
    return rows or []


# ─── agent lifecycle tools (issue #83) ───────────────────────────────────────


@mcp.tool()
def create_agent_session(session_id: str, role: str, goal: str) -> dict:
    """Registra una nueva sesión de sub-agente en la DB.

    El orquestador llama esto ANTES de hacer Popen() para que el session_id
    exista en la DB y pueda recibir eventos/comandos inmediatamente.

    Args:
        session_id: UUID del agente (asignado por el spawner).
        role:       rol del subagente (dev / ops / study / research).
        goal:       instrucción original que se le pasa al sub-agente.

    Returns:
        {"session_id": str, "status": "pending", "created_at": str}
    """
    if not session_id.strip():
        raise ValueError("session_id no puede estar vacío")
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError(
            f"session_id debe ser un UUID hex de 32 chars lowercase sin guiones: {session_id!r}"
        )
    if not role.strip():
        raise ValueError("role no puede estar vacío")
    if not goal.strip():
        raise ValueError("goal no puede estar vacío")

    _execute(
        "INSERT INTO agent_sessions (id, role, goal) VALUES (%s, %s, %s)",
        (session_id, role, goal),
    )
    row = _execute(
        "SELECT id, role, goal, status, created_at FROM agent_sessions WHERE id = %s",
        (session_id,),
        fetch="one",
    )
    _audit("create_agent_session", {"session_id": session_id, "role": role}, "ok")
    if row:
        row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    return row or {}


@mcp.tool()
def update_agent_status(
    session_id: str,
    status: str,
    pid: int | None = None,
    config_path: str | None = None,
    result_summary: str | None = None,
) -> dict:
    """Actualiza el estado y metadatos de una sesión de sub-agente.

    Llamado por el spawner en transiciones:
      pending → running  (cuando Popen() arranca, con pid)
      running → completed / failed / killed / timeout

    Args:
        session_id:     UUID de la sesión.
        status:         nuevo estado (running/completed/failed/killed/timeout).
        pid:            PID del proceso (solo al pasar a running).
        config_path:    ruta al config temporal del agente (solo al pasar a running).
        result_summary: resumen del resultado (al completar/fallar).

    Returns:
        El registro actualizado con timestamps.
    """
    valid = {"pending", "running", "completed", "failed", "killed", "timeout"}
    if status not in valid:
        raise ValueError(f"status inválido: {status!r}. Válidos: {valid}")

    started_sql = ", started_at = NOW()" if status == "running" else ""
    ended_sql = (
        ", ended_at = NOW()" if status in {"completed", "failed", "killed", "timeout"} else ""
    )

    _execute(
        f"""UPDATE agent_sessions
            SET status = %s,
                pid = COALESCE(%s, pid),
                config_path = COALESCE(%s, config_path),
                result_summary = COALESCE(%s, result_summary),
                updated_at = NOW()
                {started_sql}{ended_sql}
            WHERE id = %s""",  # noqa: S608
        (status, pid, config_path, result_summary, session_id),
    )
    row = _execute(
        """SELECT id, role, goal, status, pid, result_summary,
                  started_at, ended_at, updated_at
           FROM agent_sessions WHERE id = %s""",
        (session_id,),
        fetch="one",
    )
    _audit("update_agent_status", {"session_id": session_id, "status": status}, "ok")
    if row:
        for k in ("started_at", "ended_at", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
    return row or {}


@mcp.tool()
def list_running_agents(include_completed: bool = False) -> list[dict]:
    """Lista sub-agentes en ejecución (o todos si include_completed=True).

    Args:
        include_completed: si True, incluye también completed/failed/killed.

    Returns:
        Lista de agentes con id, role, goal, status, pid, elapsed_seconds.
    """
    if include_completed:
        sql = """
            SELECT id, role, goal, status, pid,
                   started_at,
                   EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed_seconds
            FROM agent_sessions
            ORDER BY created_at DESC
            LIMIT 50
        """
        rows = _execute(sql, fetch="all")
    else:
        rows = _execute(
            "SELECT id, role, goal, status, pid, started_at, elapsed_seconds "
            "FROM agent_sessions_active",
            fetch="all",
        )
    if rows:
        for r in rows:
            if r.get("started_at"):
                r["started_at"] = r["started_at"].isoformat()
    return rows or []


@mcp.tool()
def append_agent_event(session_id: str, kind: str, payload: dict | None = None) -> dict:
    """Añade un evento al log inmutable de un sub-agente.

    Args:
        session_id: UUID de la sesión.
        kind:       tipo de evento (spawn_requested / process_started /
                    process_ended / heartbeat / tool_called / error /
                    instruction_received / instruction_ack).
        payload:    datos adicionales del evento (dict JSON-serializable).

    Returns:
        {"id": int, "session_id": str, "kind": str, "ts": str}
    """
    valid_kinds = {
        "spawn_requested",
        "process_started",
        "process_ended",
        "heartbeat",
        "tool_called",
        "error",
        "instruction_received",
        "instruction_ack",
    }
    if kind not in valid_kinds:
        raise ValueError(f"kind inválido: {kind!r}. Válidos: {valid_kinds}")

    row = _execute(
        "INSERT INTO agent_events (session_id, kind, payload_json)"
        " VALUES (%s, %s, %s) RETURNING id, ts",
        (session_id, kind, json.dumps(payload or {})),
        fetch="one",
    )
    result = {
        "id": row["id"] if row else None,
        "session_id": session_id,
        "kind": kind,
        "ts": row["ts"].isoformat() if row and row.get("ts") else None,
    }
    return result


@mcp.tool()
def send_agent_command(session_id: str, kind: str, args: dict | None = None) -> dict:
    """Envía un comando al buzón de un sub-agente (LINA → sub-agente).

    El sub-agente lee este buzón por polling. Un NOTIFY de PostgreSQL
    despierta al sub-agente si está usando LISTEN.

    Args:
        session_id: UUID de la sesión destino.
        kind:       tipo de comando (pause / resume / kill /
                    send_instruction / set_budget).
        args:       argumentos del comando (p.ej. {"text": "Nueva instrucción"}).

    Returns:
        {"id": int, "session_id": str, "kind": str, "sent_at": str}
    """
    valid_kinds = {"pause", "resume", "kill", "send_instruction", "set_budget"}
    if kind not in valid_kinds:
        raise ValueError(f"kind inválido: {kind!r}. Válidos: {valid_kinds}")

    row = _execute(
        "INSERT INTO agent_commands (session_id, kind, args_json)"
        " VALUES (%s, %s, %s) RETURNING id, sent_at",
        (session_id, kind, json.dumps(args or {})),
        fetch="one",
    )
    _audit("send_agent_command", {"session_id": session_id, "kind": kind}, "ok")
    return {
        "id": row["id"] if row else None,
        "session_id": session_id,
        "kind": kind,
        "sent_at": row["sent_at"].isoformat() if row and row.get("sent_at") else None,
    }


@mcp.tool()
def list_agent_events(session_id: str, limit: int = 20) -> list[dict]:
    """Devuelve el log de eventos de un sub-agente (issue #88).

    Args:
        session_id: UUID del agente.
        limit:      máx. eventos a retornar (capped 1‑200).

    Returns:
        Lista de eventos con id, kind, payload_json, ts.
    """
    if not session_id.strip():
        raise ValueError("session_id no puede estar vacío")

    # Cap limit to [1, 200]
    limit = max(1, min(limit, 200))

    rows = _execute(
        "SELECT id, kind, payload_json, ts FROM agent_events"
        " WHERE session_id = %s ORDER BY id DESC LIMIT %s",
        (session_id, limit),
        fetch="all",
    )
    if not rows:
        return []

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload_json": row.get("payload_json"),
                "ts": row["ts"].isoformat() if row.get("ts") else None,
            }
        )
    return result


@mcp.tool()
def get_pending_instructions(session_id: str) -> list[dict]:
    """Lee y ackea instrucciones pendientes de un sub-agente (issue #90).

    SELECT + UPDATE (mark as read) en agent_commands.
    Solo comandos kind='send_instruction' con sent_at IS NULL.

    Args:
        session_id: UUID del agente.

    Returns:
        Lista de comandos pendientes (con kind, args_json, sent_at).
        Cada comando se marca como enviado (sent_at = NOW()).
    """
    if not session_id.strip():
        raise ValueError("session_id no puede estar vacío")

    # SELECT pending instructions
    rows = _execute(
        "SELECT id, kind, args_json, sent_at FROM agent_commands"
        " WHERE session_id = %s AND kind = 'send_instruction' AND sent_at IS NULL"
        " ORDER BY id ASC",
        (session_id,),
        fetch="all",
    )
    if not rows:
        return []

    # ACK (mark as sent)
    cmd_ids = tuple(r["id"] for r in rows)
    _execute(
        "UPDATE agent_commands SET sent_at = NOW() WHERE id IN %s",
        (cmd_ids,),
    )

    return [
        {
            "kind": r["kind"],
            "args_json": r.get("args_json"),
            "sent_at": r["sent_at"].isoformat() if r.get("sent_at") else None,
        }
        for r in rows
    ]


# ─── CLINE commands ────────────────────────────────────────────────────────────


@mcp.tool()
def write_cline_command(
    command: str,
    notification: str = "",
    args_json: dict | None = None,
) -> dict:
    """Escribe una orden para CLINE en la cola de comandos.

    LINA usa este tool para delegarle trabajo a CLINE. La orden se guarda
    con status='pending' y un daemon externo (cline-poll) la detecta y
    se la reenvia a CLINE via Telegram.

    Args:
        command:      texto de la orden (instrucciones completas para CLINE).
        notification: breve descripcion para notificaciones (opcional).
        args_json:    argumentos adicionales en JSON (opcional).

    Returns:
        {"id": int, "status": "pending", "command": str}
    """
    if not command.strip():
        raise ValueError("command no puede estar vacio")

    row = _execute(
        """INSERT INTO cline_commands (command, args_json, notification, status)
           VALUES (%s, %s, %s, 'pending')
           RETURNING id, created_at""",
        (command.strip(), json.dumps(args_json or {}), notification.strip() or None),
        fetch="one",
    )
    cmd_id = row["id"] if row else None
    # NOTIFY al daemon para que despierte instantáneamente
    try:
        _execute("NOTIFY cline_new_command, %s", (str(cmd_id),))
    except Exception:
        pass  # best-effort
    _audit("write_cline_command", {"command": command[:200], "notification": notification}, f"id={cmd_id}")
    return {
        "id": cmd_id,
        "status": "pending",
        "command": command[:200],
        "created_at": row["created_at"].isoformat() if row and row.get("created_at") else None,
    }


@mcp.tool()
def get_cline_commands(status: str = "pending", limit: int = 10) -> list[dict]:
    """Lista las ordenes en la cola de CLINE.

    Args:
        status: filtrar por status ('pending', 'running', 'completed', etc.).
                Usar 'all' para ver todas.
        limit:  maximo de resultados (1-50).

    Returns:
        Lista de ordenes con id, command, status, notification, timestamps.
    """
    if status == "all":
        status_filter = "1=1"
        params: tuple = (min(max(limit, 1), 50),)
    else:
        status_filter = "status = %s"
        params = (status, min(max(limit, 1), 50))

    rows = _execute(
        f"SELECT id, command, status, notification, created_at, started_at, completed_at, session_id"
        f" FROM cline_commands WHERE {status_filter}"
        f" ORDER BY created_at DESC LIMIT %s",
        params,
        fetch="all",
    )
    return [
        {
            "id": r["id"],
            "command": r["command"][:200],
            "status": r["status"],
            "notification": r.get("notification"),
            "session_id": r.get("session_id"),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
            "completed_at": r["completed_at"].isoformat() if r.get("completed_at") else None,
        }
        for r in rows
    ]


def log_cline_activity(order_id: int, kind: str, tool_name: str = "", detail: str = "") -> dict:
    """Registrar actividad de CLINE en tiempo real.

    Llamalo ANTES y DESPUES de cada tool call para que LINA pueda monitorear.
    El trigger NOTIFY 'cline_activity' avisa a LINA en tiempo real.

    Args:
        order_id:  id de la orden en cline_commands
        kind:      'tool_start', 'tool_end', 'thinking', 'text', 'error'
        tool_name: nombre del tool (ej: 'lina-shell-policy__sh_run')
        detail:    detalle (args, preview, resultado corto, snippet de error)
    """
    _execute(
        "INSERT INTO cline_activity (order_id, kind, tool_name, detail) VALUES (%s, %s, %s, %s)",
        (order_id, kind, tool_name, detail[:500] if detail else ""),
    )
    return {"ok": True}


def get_cline_activity(order_id: int | None = None, limit: int = 20) -> list[dict]:
    """Leer actividad de CLINE en tiempo real.

    Usalo para monitorear que esta haciendo CLINE AHORA MISMO.

    Args:
        order_id: filtrar por orden especifica. Si es None, trae las ultimas.
        limit:    maximo de entradas (1-100).
    """
    if order_id is not None:
        rows = _execute(
            "SELECT id, order_id, kind, tool_name, detail, created_at"
            " FROM cline_activity WHERE order_id = %s"
            " ORDER BY created_at DESC LIMIT %s",
            (order_id, min(max(limit, 1), 100)),
            fetch="all",
        )
    else:
        rows = _execute(
            "SELECT id, order_id, kind, tool_name, detail, created_at"
            " FROM cline_activity ORDER BY created_at DESC LIMIT %s",
            (min(max(limit, 1), 100),),
            fetch="all",
        )

    return [
        {
            "id": r["id"],
            "order_id": r["order_id"],
            "kind": r["kind"],
            "tool_name": r["tool_name"],
            "detail": r["detail"][:300] if r["detail"] else "",
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


# ─── entrypoint ───────────────────────────────────────────────────────────────


def main() -> None:
    log.info("lina-db starting (db=%s transport=%s)", LINA_DB_URL.split("@")[-1], _MCP_TRANSPORT)
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

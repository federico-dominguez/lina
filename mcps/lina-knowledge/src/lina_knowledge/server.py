"""LINA MCP — lina-knowledge.

Unified Knowledge Store with RAG + Vector Search + Multi-Agent shared memory.

Provides 3 core tools:
  - ``search_knowledge`` — Semantic search across all knowledge tables.
  - ``remember_knowledge`` — Store knowledge with auto-embedding, source, tags.
  - ``recall_knowledge`` — Hybrid text + semantic recall.

Connects to the same PostgreSQL + pgvector database as lina-db.

Transport:
    stdio by default; if MCP_TRANSPORT=streamable-http listens on 0.0.0.0:8000.

Authentication:
    LINA_DB_URL — PostgreSQL connection URL (mandatory).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import psycopg2.extras
import psycopg2.pool
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-knowledge] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-knowledge")

# ─── Config ───────────────────────────────────────────────────────────────────

LINA_DB_URL = os.environ.get("LINA_DB_URL", "")
if not LINA_DB_URL:
    log.error("LINA_DB_URL no configurada — las tools fallarán con error de conexión")

_EMBEDDING_DIMS = 1536

_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))
_POOL_MIN = int(os.environ.get("LINA_DB_POOL_MIN", "1"))
_POOL_MAX = int(os.environ.get("LINA_DB_POOL_MAX", "5"))

mcp = FastMCP(
    "lina-knowledge",
    host="0.0.0.0",
    port=_MCP_HTTP_PORT,
)

# ─── Connection pool ──────────────────────────────────────────────────────────

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            _POOL_MIN,
            _POOL_MAX,
            LINA_DB_URL,
        )
    return _pool


def _conn() -> psycopg2.extensions.connection:
    return _get_pool().getconn()


def _put_conn(conn: psycopg2.extensions.connection | None) -> None:
    if conn:
        _get_pool().putconn(conn)


def _execute(
    sql: str,
    params: tuple = (),
    *,
    fetch: str = "none",
) -> Any:
    """Execute SQL with auto cleanup. fetch='one'|'all'|'none'."""
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            conn.commit()
            return None
    except Exception:
        conn.rollback()
        raise
    finally:
        _put_conn(conn)


# ─── Embedding ────────────────────────────────────────────────────────────────


def _embed(text: str) -> list[float]:
    """Generate embedding via DeepSeek Embeddings API (text-embedding-3-small, 1536d).

    Falls back to a zero vector if the API key is not set or the request fails,
    so semantic search is degraded but doesn't crash.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        log.warning("DEEPSEEK_API_KEY no configurada — embedding será zero vector")
        return [0.0] * _EMBEDDING_DIMS

    import httpx

    try:
        resp = httpx.post(
            "https://api.deepseek.com/beta/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "text-embedding-3-small",
                "input": text,
                "encoding_format": "float",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as exc:
        log.error("Embedding API error: %s", exc)
        return [0.0] * _EMBEDDING_DIMS


def _embedding_to_pgvector(embedding: list[float]) -> str:
    """Format a float list as a pgvector string literal."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


# ─── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def remember_knowledge(
    key: str,
    value: str,
    source: str = "manual",
    tags: str | None = None,
    agent: str | None = None,
    ttl_hours: int | None = None,
) -> str:
    """Store a piece of knowledge with automatic vector embedding.

    The knowledge is indexed in ``knowledge_store`` with a 1536-dim embedding
    generated via DeepSeek Embeddings API. All agents can later search for it.

    Args:
        key:       Unique identifier for this knowledge (e.g. "bug_ogg_double_ext").
        value:     Content to remember (e.g. "El archivo se guardaba con doble extensión ..ogg en telegram_client.py").
        source:    Origin: chat, web, repo, preference, manual (default: manual).
        tags:      Comma-separated tags for filtering (e.g. "bug,telegram,audio").
        agent:     Agent that created this (lina, gemma, cline, goose). Default: None.
        ttl_hours: Time-to-live in hours. None = permanent.

    Returns:
        "ok" if new, "updated" if overwritten.
    """
    if not key.strip():
        raise ValueError("key no puede estar vacía")
    if not value.strip():
        raise ValueError("value no puede estar vacío")

    emb = _embed(value)
    emb_str = _embedding_to_pgvector(emb)

    tags_list: list[str] = [t.strip() for t in (tags or "").split(",") if t.strip()]

    if ttl_hours is not None and ttl_hours > 0:
        ttl_sql = "NOW() + (%s * INTERVAL '1 hour')"
        ttl_params = (ttl_hours,)
    else:
        ttl_sql = "NULL"
        ttl_params = ()

    existing = _execute(
        "SELECT id FROM knowledge_store WHERE key = %s",
        (key,),
        fetch="one",
    )

    if existing:
        _execute(
            f"UPDATE knowledge_store SET value = %s, embedding = %s::vector, source = %s, "
            f"tags = %s::text[], agent = %s, ttl = {ttl_sql}, updated_at = NOW() "
            f"WHERE key = %s",
            (value, emb_str, source, tags_list, agent) + ttl_params + (key,),
        )
        return "updated"

    _execute(
        f"INSERT INTO knowledge_store (key, value, embedding, source, tags, agent, ttl) "
        f"VALUES (%s, %s, %s::vector, %s, %s::text[], %s, {ttl_sql})",
        (key, value, emb_str, source, tags_list, agent) + ttl_params,
    )
    return "ok"


@mcp.tool()
def search_knowledge(
    query: str,
    limit: int = 10,
    sources: str | None = None,
    threshold: float = 0.0,
) -> str:
    """Search across all knowledge sources using semantic vector search.

    Queries ``knowledge_store``, ``session_summaries``, and ``repo_index``
    tables, ranked by cosine similarity to the query embedding.

    Args:
        query:     Natural language search query.
        limit:     Max results per source (default: 10, max: 50).
        sources:   Comma-separated source filter: "knowledge", "sessions", "repo".
                   Default: all sources.
        threshold: Minimum similarity threshold 0.0-1.0 (default: 0.0).

    Returns:
        JSON string with results grouped by source.
    """
    if not query.strip():
        return json.dumps({"error": "query no puede estar vacía"}, ensure_ascii=False)

    limit = min(max(limit, 1), 50)
    emb = _embed(query)
    emb_str = _embedding_to_pgvector(emb)

    allowed_sources = {
        s.strip().lower() for s in (sources or "knowledge,sessions,repo").split(",")
    }
    results: dict[str, list[dict[str, Any]]] = {}

    # 1. knowledge_store
    if "knowledge" in allowed_sources:
        rows = _execute(
            "SELECT key, value, source, agent, tags, "
            f"1 - (embedding <=> %s::vector) AS similarity "
            "FROM knowledge_store "
            "WHERE embedding IS NOT NULL "
            "AND (ttl IS NULL OR ttl > NOW()) "
            f"AND 1 - (embedding <=> %s::vector) >= %s "
            "ORDER BY similarity DESC LIMIT %s",
            (emb_str, emb_str, threshold, limit),
            fetch="all",
        )
        results["knowledge_store"] = [
            {
                "key": r["key"],
                "value": r["value"][:500],
                "source": r["source"],
                "agent": r["agent"],
                "tags": r["tags"],
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in rows or []
        ]

    # 2. session_summaries
    if "sessions" in allowed_sources:
        rows = _execute(
            "SELECT session_id, raw_summary, topics, facts, pending, agent, "
            f"1 - (embedding <=> %s::vector) AS similarity "
            "FROM session_summaries "
            "WHERE embedding IS NOT NULL "
            f"AND 1 - (embedding <=> %s::vector) >= %s "
            "ORDER BY similarity DESC LIMIT %s",
            (emb_str, emb_str, threshold, limit),
            fetch="all",
        )
        results["session_summaries"] = [
            {
                "session_id": r["session_id"],
                "summary": r["raw_summary"][:500],
                "topics": r["topics"],
                "facts": r["facts"],
                "pending": r["pending"],
                "agent": r["agent"],
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in rows or []
        ]

    # 3. repo_index
    if "repo" in allowed_sources:
        rows = _execute(
            "SELECT file_path, content, language, chunk_index, total_chunks, "
            f"1 - (embedding <=> %s::vector) AS similarity "
            "FROM repo_index "
            "WHERE embedding IS NOT NULL "
            f"AND 1 - (embedding <=> %s::vector) >= %s "
            "ORDER BY similarity DESC LIMIT %s",
            (emb_str, emb_str, threshold, limit),
            fetch="all",
        )
        results["repo_index"] = [
            {
                "file_path": r["file_path"],
                "content": r["content"][:300],
                "language": r["language"],
                "chunk": f"{r['chunk_index'] + 1}/{r['total_chunks']}",
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in rows or []
        ]

    return json.dumps(results, ensure_ascii=False, default=str)


@mcp.tool()
def recall_knowledge(
    query: str,
    limit: int = 10,
    source: str | None = None,
    agent: str | None = None,
) -> str:
    """Hybrid text + semantic recall. Searches by keyword in knowledge_store,
    then ranks by semantic similarity.

    Args:
        query:  Search query (used for both text ILIKE and semantic search).
        limit:  Max results (default: 10, max: 50).
        source: Filter by source (chat, web, repo, preference, manual). None = all.
        agent:  Filter by agent (lina, gemma, cline, goose). None = all.

    Returns:
        JSON string with matching knowledge entries.
    """
    if not query.strip():
        return json.dumps({"error": "query no puede estar vacía"}, ensure_ascii=False)

    limit = min(max(limit, 1), 50)
    emb = _embed(query)
    emb_str = _embedding_to_pgvector(emb)

    # Build filters
    conditions = ["(ttl IS NULL OR ttl > NOW())"]
    params: list[Any] = []

    if source:
        conditions.append("source = %s")
        params.append(source)
    if agent:
        conditions.append("agent = %s")
        params.append(agent)

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    rows = _execute(
        f"SELECT key, value, source, agent, tags, ttl, created_at, "
        f"1 - (embedding <=> %s::vector) AS similarity, "
        f"value ILIKE %s AS text_match "
        f"FROM knowledge_store "
        f"WHERE {where_clause} "
        f"ORDER BY text_match DESC, similarity DESC LIMIT %s",
        (emb_str,) + tuple(params) + (f"%{query}%", limit),
        fetch="all",
    )

    return json.dumps(
        [
            {
                "key": r["key"],
                "value": r["value"][:500],
                "source": r["source"],
                "agent": r["agent"],
                "tags": r["tags"],
                "similarity": round(float(r["similarity"]), 4),
                "text_match": bool(r["text_match"]),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "expires_at": r["ttl"].isoformat() if r.get("ttl") else None,
            }
            for r in rows or []
        ],
        ensure_ascii=False,
        default=str,
    )


@mcp.tool()
def get_knowledge_stats() -> str:
    """Get statistics about the knowledge store.

    Returns:
        JSON with counts per source, agent, and total entries.
    """
    counts = _execute(
        "SELECT source, COUNT(*) AS cnt FROM knowledge_store GROUP BY source ORDER BY cnt DESC",
        fetch="all",
    )
    agents = _execute(
        "SELECT agent, COUNT(*) AS cnt FROM knowledge_store GROUP BY agent ORDER BY cnt DESC",
        fetch="all",
    )
    total = _execute("SELECT COUNT(*) AS cnt FROM knowledge_store", fetch="one")
    expired = _execute(
        "SELECT COUNT(*) AS cnt FROM knowledge_store WHERE ttl IS NOT NULL AND ttl <= NOW()",
        fetch="one",
    )
    sessions = _execute("SELECT COUNT(*) AS cnt FROM session_summaries", fetch="one")
    repo_chunks = _execute("SELECT COUNT(*) AS cnt FROM repo_index", fetch="one")

    return json.dumps(
        {
            "total_knowledge_entries": total["cnt"] if total else 0,
            "expired_entries": expired["cnt"] if expired else 0,
            "by_source": {r["source"]: r["cnt"] for r in counts or []},
            "by_agent": {r["agent"] or "null": r["cnt"] for r in agents or []},
            "session_summaries": sessions["cnt"] if sessions else 0,
            "repo_index_chunks": repo_chunks["cnt"] if repo_chunks else 0,
        },
        ensure_ascii=False,
        default=str,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    log.info(
        "lina-knowledge starting (db=%s transport=%s)",
        LINA_DB_URL.split("@")[-1] if "@" in LINA_DB_URL else LINA_DB_URL,
        _MCP_TRANSPORT,
    )
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

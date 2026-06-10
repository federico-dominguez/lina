"""Tests for LINA Google Fit MCP — cache & helpers."""

import pytest
from datetime import date
from lina_google_fit.store import set_cache, get_cached, _connect, _ensure_table


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM lina.health_cache")
            conn.commit()
    finally:
        conn.close()


@pytest.mark.db
def test_ensure_table():
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema='lina' AND table_name='health_cache')")
            assert cur.fetchone()[0] is True
    finally:
        conn.close()


@pytest.mark.db
def test_cache_write_and_read():
    set_cache("steps", date(2026, 6, 10), {"value": 8432})
    result = get_cached("steps", date(2026, 6, 10))
    assert result is not None
    assert result["value"]["value"] == 8432


@pytest.mark.db
def test_cache_miss():
    result = get_cached("steps", date(2024, 1, 1))
    assert result is None


def test_store_imports():
    """Basic non-DB test verifying store module imports correctly."""
    from lina_google_fit import store
    assert store is not None  # module imports OK

"""Tests for LINA User Profile MCP."""

import pytest
from lina_user.store import set_profile, get_profile, delete_profile, _connect, _ensure_table


@pytest.fixture(autouse=True)
def _cleanup():
    """Clean test data after each test."""
    yield
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM lina.user_profile WHERE key LIKE 'test_%'")
            conn.commit()
    finally:
        conn.close()


def test_ensure_table():
    """Table should be created on first access."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema='lina' AND table_name='user_profile')")
            assert cur.fetchone()[0] is True
    finally:
        conn.close()


def test_set_and_get():
    """Set a field and retrieve it."""
    set_profile("test_nombre", "Fede")
    result = get_profile("test_nombre")
    assert result["found"] is True
    assert result["value"] == "Fede"
    assert result["key"] == "test_nombre"


def test_set_json_value():
    """Set a JSON value (nested dict)."""
    set_profile("test_meta", {"libros": 12, "ejercicio": "3x semana"})
    result = get_profile("test_meta")
    assert result["value"]["libros"] == 12
    assert result["value"]["ejercicio"] == "3x semana"


def test_get_nonexistent():
    """Getting a nonexistent key returns found=False."""
    result = get_profile("test_no_existe_xyz_123")
    assert result["found"] is False
    assert result["value"] is None


def test_list_all():
    """List all profile fields."""
    set_profile("test_a", "alpha")
    set_profile("test_b", "beta")
    results = get_profile(None)
    keys = [r["key"] for r in results]
    assert "test_a" in keys
    assert "test_b" in keys


def test_update_existing():
    """Updating an existing field changes the value."""
    set_profile("test_update", "v1")
    set_profile("test_update", "v2")
    result = get_profile("test_update")
    assert result["value"] == "v2"


def test_delete():
    """Delete a field."""
    set_profile("test_delete", "bye")
    result = delete_profile("test_delete")
    assert result["deleted"] is True
    result2 = get_profile("test_delete")
    assert result2["found"] is False

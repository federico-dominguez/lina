"""Tests para la migration 011: dead-letter queue (issue #89)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "sql" / "migrations"


def test_migration_011_exists():
    """Migration 011-dead-letter.sql existe."""
    path = MIGRATIONS / "011-dead-letter.sql"
    assert path.is_file(), f"Migration not found: {path}"


def test_migration_011_has_dead_letter_table():
    """Migration crea la tabla dead_letter."""
    sql = (MIGRATIONS / "011-dead-letter.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS dead_letter" in sql


def test_migration_011_has_retry_count():
    """Migration agrega retry_count a agent_sessions."""
    sql = (MIGRATIONS / "011-dead-letter.sql").read_text()
    assert "retry_count" in sql
    assert "ADD COLUMN IF NOT EXISTS retry_count" in sql


def test_migration_011_has_pg_notify_trigger():
    """Migration crea trigger de notificación."""
    sql = (MIGRATIONS / "011-dead-letter.sql").read_text()
    assert "agent_status_change_notify" in sql
    assert "pg_notify" in sql


def test_migration_011_has_failed_agents_view():
    """Migration crea vista failed_agents."""
    sql = (MIGRATIONS / "011-dead-letter.sql").read_text()
    assert "CREATE OR REPLACE VIEW failed_agents" in sql

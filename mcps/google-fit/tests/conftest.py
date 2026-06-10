"""pytest conftest: skip DB-dependent tests when PostgreSQL is unavailable."""

import pytest


def _pg_available() -> bool:
    try:
        import psycopg2  # type: ignore[import-untyped]
        try:
            conn = psycopg2.connect(
                "dbname=lina user=lina password=lina_dev host=localhost port=5432",
                connect_timeout=2,
            )
            conn.close()
            return True
        except Exception:
            return False
    except ImportError:
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: marks tests that need PostgreSQL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _pg_available():
        return  # DB available — run all tests
    skip_db = pytest.mark.skip(reason="PostgreSQL not available")
    for item in items:
        if item.get_closest_marker("db"):
            item.add_marker(skip_db)

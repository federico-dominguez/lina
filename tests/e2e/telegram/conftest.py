"""
pytest fixtures for Telegram E2E tests.

The `tg` fixture provides a connected TelegramTestClient.  It is session-scoped
so Telethon authenticates once per pytest run.

Mark tests with @pytest.mark.e2e_telegram.  They are excluded from the
default CI run; invoke with:

    pytest -m e2e_telegram tests/e2e/telegram/

Required env vars: see tests/e2e/telegram/client.py module docstring.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from .client import TelegramTestClient


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e_telegram: Telegram E2E tests — require a real Telegram account "
        "and a running Lina instance. Not run in CI by default.",
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def tg() -> TelegramTestClient:  # type: ignore[misc]
    """Session-scoped connected TelegramTestClient."""
    async with TelegramTestClient() as client:
        yield client

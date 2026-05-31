"""Configuration loaded from environment variables."""

from __future__ import annotations

import os


class Config:
    """All settings for lina-gateway loaded from environment variables."""

    # --- Telegram ---
    bot_token: str
    # Comma-separated list of "telegram:<chat_id>" trusted users (auto-paired).
    trusted_users: frozenset[str]
    # Maximum voice file size we'll attempt to process (bytes).
    max_voice_bytes: int

    # --- goosed ---
    goosed_url: str  # e.g. http://lina-goosed:3000
    goosed_secret: str  # GOOSE_SERVER_SECRET_KEY
    goosed_connect_timeout: float
    goosed_read_timeout: float

    # --- Behaviour ---
    # Interval (seconds) between editMessageText calls (pacer tick).
    pacer_tick: float
    poll_timeout: int  # Telegram long-poll timeout in seconds

    # --- Session recovery (issue #49) ---
    # PostgreSQL DSN for the lina-db instance.  Optional — boot hook is disabled
    # when unset (gateway operates normally, just without crash detection).
    lina_db_url: str | None
    # DeepSeek API key for balance snapshot tracking (issue #61).
    deepseek_api_key: str | None
    # Telegram chat IDs that receive boot/shutdown notifications.
    # Derived from GOOSE_GATEWAY_TRUSTED_USERS (same format: "telegram:<id>")
    # unless overridden via GATEWAY_NOTIFY_CHAT_IDS (comma-separated integers).
    notify_chat_ids: list[int]

    def __init__(self) -> None:
        self.bot_token = _require("TELEGRAM_BOT_TOKEN")
        raw_trusted = os.environ.get("GOOSE_GATEWAY_TRUSTED_USERS", "")
        self.trusted_users = frozenset(p.strip() for p in raw_trusted.split(",") if p.strip())
        self.max_voice_bytes = int(os.environ.get("MAX_VOICE_BYTES", str(20 * 1024 * 1024)))

        self.goosed_url = os.environ.get("GOOSED_URL", "http://lina-goosed:3000").rstrip("/")
        self.goosed_secret = os.environ.get("GOOSE_SERVER_SECRET_KEY", "")
        self.goosed_connect_timeout = float(os.environ.get("GOOSED_CONNECT_TIMEOUT", "10"))
        self.goosed_read_timeout = float(os.environ.get("GOOSED_READ_TIMEOUT", "300"))

        self.pacer_tick = float(os.environ.get("GATEWAY_PACER_TICK", "1.5"))
        self.poll_timeout = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "30"))

        self.lina_db_url = os.environ.get("LINA_DB_URL") or None
        self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY") or None

        # GATEWAY_NOTIFY_CHAT_IDS takes priority; falls back to extracting numeric
        # IDs from GOOSE_GATEWAY_TRUSTED_USERS ("telegram:<id>" entries).
        raw_notify = os.environ.get("GATEWAY_NOTIFY_CHAT_IDS", "")
        if raw_notify.strip():
            self.notify_chat_ids = [
                int(x.strip()) for x in raw_notify.split(",") if x.strip().lstrip("-").isdigit()
            ]
        else:
            self.notify_chat_ids = [
                int(p.split(":")[1])
                for p in self.trusted_users
                if p.startswith("telegram:") and p.split(":")[1].lstrip("-").isdigit()
            ]

    def is_trusted(self, chat_id: int) -> bool:
        return f"telegram:{chat_id}" in self.trusted_users


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value

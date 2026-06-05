"""Configuration loaded from environment variables.

Supports two modes:
  a) **Legacy single-bot**: env vars TELEGRAM_BOT_TOKEN / LINA_BOT_TOKEN / etc.
  b) **Multi-bot**: env var MULTI_BOT_COUNT=N + BOT_1_NAME, BOT_1_TOKEN, etc.

In multi-bot mode a single gateway process handles N Telegram bots,
each connecting to (potentially different) goosed endpoints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ─── Per-bot configuration ───────────────────────────────────────────────────


@dataclass
class BotConfig:
    """Settings for one Telegram bot instance."""

    name: str  # Human-readable bot name (LINA, Cline, Goose, …)
    bot_token: str
    bot_username: str  # e.g. "s_lina_bot" — used for @-mention detection
    goosed_url: str  # goosed endpoint this bot connects to
    goosed_secret: str
    trusted_users: frozenset[str] = field(default_factory=frozenset)
    notify_chat_ids: list[int] = field(default_factory=list)
    observe_port: int = 9090  # each bot exposes events on a different WS port

    # Override session_id — when set, ALL chats for this bot share one session.
    # Useful for the "Goose" personality that bridges a local desktop agent.
    fixed_session_id: str | None = None

    def is_trusted(self, chat_id: int) -> bool:
        return f"telegram:{chat_id}" in self.trusted_users


# ─── Shared / global configuration ──────────────────────────────────────────


@dataclass
class SharedConfig:
    lina_db_url: str | None = None
    deepseek_api_key: str | None = None
    pacer_tick: float = 1.5
    poll_timeout: int = 30
    goosed_connect_timeout: float = 10.0
    goosed_read_timeout: float = 300.0
    max_voice_bytes: int = 20 * 1024 * 1024
    agent_poll_interval: float = 10.0


# ─── Top-level config ────────────────────────────────────────────────────────


class Config:
    """Holds all settings: one SharedConfig + list of BotConfig."""

    shared: SharedConfig
    bots: list[BotConfig]

    def __init__(self) -> None:
        self.shared = SharedConfig()
        self.bots = self._discover_bots()
        self._load_shared()

        if not self.bots:
            raise RuntimeError(
                "No bots configured. Set MULTI_BOT_COUNT=1 + BOT_1_TOKEN=… "
                "or a legacy env var (TELEGRAM_BOT_TOKEN / LINA_BOT_TOKEN / …)."
            )

    # ── Shared settings ─────────────────────────────────────────────────────

    def _load_shared(self) -> None:
        s = self.shared
        s.lina_db_url = os.environ.get("LINA_DB_URL") or None
        s.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY") or None
        s.pacer_tick = float(os.environ.get("GATEWAY_PACER_TICK", "1.5"))
        s.poll_timeout = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "30"))
        s.goosed_connect_timeout = float(os.environ.get("GOOSED_CONNECT_TIMEOUT", "10"))
        s.goosed_read_timeout = float(os.environ.get("GOOSED_READ_TIMEOUT", "300"))
        s.max_voice_bytes = int(os.environ.get("MAX_VOICE_BYTES", str(20 * 1024 * 1024)))
        s.agent_poll_interval = float(os.environ.get("GATEWAY_AGENT_POLL_INTERVAL", "10"))

    # ── Bot discovery ───────────────────────────────────────────────────────

    @staticmethod
    def _discover_bots() -> list[BotConfig]:
        """Try multi-bot env vars first; fall back to legacy single-bot vars."""
        bots = Config._discover_multi_bot()
        if bots:
            return bots
        legacy = Config._discover_legacy_single_bot()
        if legacy:
            return [legacy]
        return []

    @staticmethod
    def _discover_multi_bot() -> list[BotConfig]:
        """Read MULTI_BOT_COUNT + BOT_1_* … BOT_N_* env vars."""
        raw = os.environ.get("MULTI_BOT_COUNT", "").strip()
        if not raw.isdigit():
            return []
        count = int(raw)
        if count < 1:
            return []
        bots: list[BotConfig] = []
        for i in range(1, count + 1):
            prefix = f"BOT_{i}_"
            token = _require(f"{prefix}TOKEN")
            name = _env(f"{prefix}NAME", default=f"bot-{i}")
            username = _env(f"{prefix}USERNAME", default="")
            goosed_url = _env(
                f"{prefix}GOOSED_URL",
                default=os.environ.get("GOOSED_URL", "http://lina-goosed:3000"),
            ).rstrip("/")
            goosed_secret = _env(f"{prefix}GOOSED_SECRET", default="")
            raw_trusted = _env(f"{prefix}TRUSTED_USERS", default="")
            trusted = frozenset(p.strip() for p in raw_trusted.split(",") if p.strip())
            raw_notify = _env(f"{prefix}NOTIFY_CHAT_IDS", default="")
            notify = [
                int(x.strip()) for x in raw_notify.split(",") if x.strip().lstrip("-").isdigit()
            ]
            # Fallback: extract numeric IDs from trusted users
            if not notify:
                notify = [
                    int(p.split(":")[1])
                    for p in trusted
                    if p.startswith("telegram:") and p.split(":")[1].lstrip("-").isdigit()
                ]
            observe_port = int(_env(f"{prefix}OBSERVE_PORT", default="9090"))
            fixed_session = _env(f"{prefix}FIXED_SESSION_ID", default=None)
            bots.append(
                BotConfig(
                    name=name,
                    bot_token=token,
                    bot_username=username,
                    goosed_url=goosed_url,
                    goosed_secret=goosed_secret,
                    trusted_users=trusted,
                    notify_chat_ids=notify,
                    observe_port=observe_port,
                    fixed_session_id=fixed_session,
                )
            )
        return bots

    @staticmethod
    def _discover_legacy_single_bot() -> BotConfig | None:
        """Backward-compatible single-bot mode — read old env vars."""
        token = _try_any(
            "LINA_BOT_TOKEN",
            "CLINE_BOT_TOKEN",
            "GOOSE_BOT_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "DESKTOP_BOT_TOKEN",
        )
        if not token:
            return None

        name = _env("GATEWAY_BOT_NAME", default="LINA")
        goosed_url = os.environ.get("GOOSED_URL", "http://lina-goosed:3000").rstrip("/")
        goosed_secret = os.environ.get("GOOSE_SERVER_SECRET_KEY", "")
        raw_trusted = (
            _try_any(
                "LINA_TRUSTED_USERS",
                "CLINE_TRUSTED_USERS",
                "GOOSE_TRUSTED_USERS",
                "GOOSE_GATEWAY_TRUSTED_USERS",
                default="",
            )
            or ""
        )
        trusted = frozenset(p.strip() for p in raw_trusted.split(",") if p.strip())

        raw_notify = (
            _try_any(
                "LINA_NOTIFY_CHAT_IDS",
                "CLINE_NOTIFY_CHAT_IDS",
                "GOOSE_NOTIFY_CHAT_IDS",
                "GOOSE_BOT_NOTIFY_CHAT_IDS",
                "GATEWAY_NOTIFY_CHAT_IDS",
                default="",
            )
            or ""
        )
        if raw_notify.strip():
            notify = [
                int(x.strip()) for x in raw_notify.split(",") if x.strip().lstrip("-").isdigit()
            ]
        else:
            notify = [
                int(p.split(":")[1])
                for p in trusted
                if p.startswith("telegram:") and p.split(":")[1].lstrip("-").isdigit()
            ]

        observe_port = int(os.environ.get("OBSERVE_PORT", "9090"))
        fixed_session = os.environ.get("GOOSE_FIXED_SESSION_ID") or None
        username = _env("GATEWAY_BOT_USERNAME", default="")

        return BotConfig(
            name=name,
            bot_token=token,
            bot_username=username,
            goosed_url=goosed_url,
            goosed_secret=goosed_secret,
            trusted_users=trusted,
            notify_chat_ids=notify,
            observe_port=observe_port,
            fixed_session_id=fixed_session,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _env(*names: str, default: str | None = None) -> str | None:
    """Return the first env var from *names that is set and non-empty."""
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


def _require(*names: str) -> str:
    """Like _env but raises if none are set (also checks secrets file)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
        value = _try_secrets_file(name)
        if value:
            return value
    raise RuntimeError(
        f"Required environment variable ({' | '.join(repr(n) for n in names)}) is not set"
    )


def _try_any(*names: str, default: str | None = None) -> str | None:
    """Return first env var found, or *default if none set."""
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


def _try_secrets_file(name: str) -> str | None:
    """Try reading from lina-secrets file backend (volume-mounted at /run/secrets/lina)."""
    secrets_dir = "/run/secrets/lina"
    if not os.path.isdir(secrets_dir):
        return None
    _SECRETS_MAP: dict[str, str] = {
        "TELEGRAM_BOT_TOKEN": "telegram__bot_token",
        "TELEGRAM_API_ID": "telegram__api_id",
        "TELEGRAM_API_HASH": "telegram__api_hash",
        "TELEGRAM_BOT_USERNAME": "telegram__bot_username",
        "TELEGRAM_PHONE": "telegram__phone",
        "DEEPSEEK_API_KEY": "deepseek__api_key",
    }
    file_key = _SECRETS_MAP.get(name)
    if not file_key:
        return None
    filepath = os.path.join(secrets_dir, file_key)
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read().rstrip("\n")
        return content if content else None
    except OSError:
        return None

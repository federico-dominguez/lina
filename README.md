# LINA — Local Intelligent Network Agent

> Personal autonomous AI agent powered by **Goose + DeepSeek V4 + Telegram**.  
> Runs on your Linux desktop, talks to you via Telegram (text & voice),  
> reasons deeply, executes tasks, and extends itself.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quickstart](#quickstart)
- [Configuration Reference](#configuration-reference)
- [MCP Catalog](#mcp-catalog)
- [Development](#development)
- [Contributing](#contributing)
- [Security](#security)
- [Roadmap](#roadmap)
- [Changelog](#changelog)

---

## Overview

LINA is a fully local, privacy-first AI agent. It uses:

| Layer           | Technology                                    |
|-----------------|-----------------------------------------------|
| Agent runtime   | [Goose](https://github.com/block/goose) (patched build) |
| AI model        | DeepSeek V4 (`deepseek-chat` / `deepseek-reasoner`) with **thinking always ON** |
| User interface  | Telegram gateway — text + voice notes (Whisper) |
| Extensibility   | Custom MCP servers (Python, Clean Architecture) |
| Secrets         | `libsecret` / kwallet via `lina-secrets` MCP  |
| Init system     | systemd user service (`lina-goosed.service`)  |

LINA is designed to be:
- **Autonomous** — multi-turn reasoning, tool use, self-correction.
- **Private** — all computation on your hardware; only LLM inference leaves the machine.
- **Extensible** — each capability is a separate MCP server you own.
- **Opinionated** — Clean Architecture enforced in every MCP; no ad-hoc scripts.

---

## Architecture

```
Telegram (user)
      │ voice/text
      ▼
┌─────────────────────────────────────┐
│  goosed  (Goose agent daemon)        │
│  ┌─────────────────────────────────┐ │
│  │  DeepSeek V4  (thinking: ON)    │ │
│  └──────────┬──────────────────────┘ │
│             │ tool calls             │
│  ┌──────────▼──────────────────────┐ │
│  │  MCP servers (stdio)            │ │
│  │  lina-secrets   lina-fs-safe    │ │
│  │  lina-shell     lina-systemd    │ │
│  │  lina-moodle    (+ future MCPs) │ │
│  └─────────────────────────────────┘ │
└─────────────────────────────────────┘
      │ systemd user service
      ▼
  lina-goosed.service  (~/lina/deploy/systemd/)
```

Full architecture decisions: [`docs/architecture/`](docs/architecture/)

---

## Requirements

| Tool      | Version  | Notes                                         |
|-----------|----------|-----------------------------------------------|
| Linux     | Ubuntu 22.04+ / any systemd distro            |
| `just`    | ≥ 1.25   | `cargo install just`                          |
| `uv`      | ≥ 0.4    | Auto-installed by bootstrap                   |
| `systemd` | user units enabled (`loginctl enable-linger`) |
| `goosed`  | patched build | See [building goosed](#building-goosed) |
| `adb`     | optional | For Android control MCP (ADR 0003)            |

DeepSeek API key and a Telegram bot token are required (see [Quickstart](#quickstart)).

---

## Quickstart

```bash
# 1. Clone
git clone git@github.com:<your-user>/lina.git ~/lina
cd ~/lina

# 2. Bootstrap (installs uv, syncs MCP deps, installs systemd unit)
just bootstrap

# 3. Configure secrets interactively
just secrets-init
# → prompts for DEEPSEEK_API_KEY and TELEGRAM_BOT_TOKEN

# 4. Render Goose config from template
just apply-config

# 5. Start the service
just start

# 6. Health check
just doctor
```

Talk to LINA via the Telegram bot you configured. Send a voice note or text.

---

## Configuration Reference

All runtime configuration is environment-based.  
Secrets live in `~/.config/goose/secrets.env` (chmod 600, never committed).

Copy `.env.example` to understand available variables:

```bash
cp .env.example ~/.config/goose/secrets.env
# then fill in your values
```

Key variables:

| Variable                   | Default             | Description                                  |
|----------------------------|---------------------|----------------------------------------------|
| `DEEPSEEK_API_KEY`         | —                   | Required. DeepSeek API key.                  |
| `TELEGRAM_BOT_TOKEN`       | —                   | Required. Telegram bot token.                |
| `GOOSED_BINARY`            | `~/lina/bin/goosed` | Path to the Goose daemon binary.             |
| `GOOSE_MAX_TURNS`          | `1000`              | Max agent turns per session.                 |
| `GOOSE_GATEWAY_MAX_TURNS`  | `200`               | Max turns per gateway request.               |
| `LINA_FS_ALLOWLIST`        | `~/lina:~/Documents`| Colon-separated dirs `fs-safe` can touch.    |
| `LINA_SHELL_ALLOW_SUDO`    | `0`                 | Set to `1` only during maintenance windows.  |
| `LINA_SHELL_TIMEOUT_SEC`   | `60`                | Max seconds a shell command may run.         |

---

## MCP Catalog

MCPs follow Clean Architecture: `domain/ → application/ → infrastructure/ → server.py`.

| MCP                  | Status        | Description                                              |
|----------------------|---------------|----------------------------------------------------------|
| `lina-secrets`       | ✅ Implemented | Read/write secrets in system keyring (libsecret/kwallet) |
| `lina-fs-safe`       | ✅ Implemented | Sandboxed file I/O within `LINA_FS_ALLOWLIST`            |
| `lina-shell-policy`  | ✅ Implemented | Audited shell execution with allowlist + timeout         |
| `lina-systemd-user`  | ✅ Implemented | Manage user systemd services (start/stop/status)         |
| `lina-moodle`        | ✅ Implemented | Interact with Moodle LMS (courses, quizzes, submissions) |
| `lina-git-ops`       | 🗓 Planned     | Git operations on allowed repos                          |
| `lina-self-modify`   | 🗓 Planned     | LINA modifies her own prompts/recipes                    |
| `lina-cost-meter`    | 🗓 Planned     | Track DeepSeek API usage and cost                        |
| `lina-android-remote`| 🗓 Planned     | Control Android phone via ADB (ADR 0003)                 |

See [`config/mcp-registry.yaml`](config/mcp-registry.yaml) for the full registry.

---

## Development

### Project structure

```
lina/
├── bin/               # Executable scripts (lina, lina-doctor, lina-log)
├── config/            # Goose config template + MCP registry
├── deploy/
│   ├── systemd/       # lina-goosed.service unit file
│   ├── ansible/       # (planned) remote deploy
│   └── docker/        # (planned) container packaging
├── docs/
│   ├── architecture/  # ADRs (Architecture Decision Records)
│   ├── runbooks/      # Operational runbooks
│   └── analysis/      # Session analysis and retrospectives
├── domain/            # Pure domain entities, events, policies (no deps)
├── application/       # Use-case orchestration (no framework deps)
├── infrastructure/    # External adapters (Telegram gateway, providers…)
├── mcps/              # One subdirectory per MCP server
│   └── <name>/
│       ├── pyproject.toml
│       ├── uv.lock
│       └── src/lina_<name>/
│           ├── domain/
│           ├── application/
│           ├── infrastructure/
│           └── server.py
├── prompts/           # System + persona prompts for Goose
├── recipes/           # Goose recipe YAML files
├── skills/            # Reusable skill definitions
├── tests/
│   ├── unit/
│   ├── integration/mcps/
│   └── e2e/telegram/
└── vendor/            # Pinned third-party assets (checksummed)
```

### Building a new MCP

```bash
# Scaffold (mirrors existing MCPs)
mkdir -p mcps/my-mcp/src/lina_my_mcp/{domain,application,infrastructure}
touch mcps/my-mcp/src/lina_my_mcp/{__init__.py,server.py}

# Minimal pyproject.toml — see mcps/fs-safe/pyproject.toml for reference
# Register in config/mcp-registry.yaml
# Add to goose.config.yaml.tmpl under extensions:
```

### Running tests

```bash
# Unit tests (all)
just test

# Single MCP
cd mcps/fs-safe && uv run pytest

# Integration (requires running services)
just test-integration
```

### Building goosed

The Goose daemon needs to be compiled from the patched fork:

```bash
git clone https://github.com/block/goose ~/src/goose
cd ~/src/goose

# Build without llama-cpp (avoids clang/LLVM dep, saves ~20 min)
cargo build --release --bin goosed \
  --no-default-features \
  --features portable-default,system-keyring,code-mode

# Symlink into LINA
ln -sfn ~/src/goose/target/release/goosed ~/goosed_patched
just _symlink-goosed
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Security

See [SECURITY.md](SECURITY.md).  
Report vulnerabilities privately via GitHub Security Advisories.

---

## Roadmap

| Wave | Focus                                          | Status          |
|------|------------------------------------------------|-----------------|
| 1    | Sovereignty (secrets, fs, shell, systemd)      | ✅ Done          |
| 2    | Self-extension (git-ops, self-modify, cost)    | 🚧 In progress   |
| 3    | Integrations (Moodle, email, calendar)         | 🚧 In progress   |
| 4    | Android control (ADR 0003)                     | 🗓 Planned       |
| 5    | Multi-agent / sub-agent coordination           | 🗓 Planned       |

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

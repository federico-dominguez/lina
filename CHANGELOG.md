# Changelog

All notable changes to LINA are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Versioning follows [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`.

> **Note:** Prior to v1.0.0, LINA was developed as a personal unversioned project.
> This changelog records the state at initial repository publication and going forward.

---

## [Unreleased]

### Added
- ADR 0003: Android control via ADB bridge (hybrid architecture, MCP `lina-android-remote` planned)
- ADR 0002: UI rendering strategy (Telegram HTML rendering)

### Changed
- Repository migrated to private GitHub with full professional structure
- Questionnaire output files moved to `~/Documentos/lina-moodle-exports/`

### Fixed
- **Telegram final message truncated at 4096 chars** — `edit_text` with `sealed=true`
  now calls `split_message` and sends remaining content as follow-up messages instead
  of silently dropping it with `…`. Live bubbles (`sealed=false`) keep the safe truncation
  behaviour (Telegram does not allow splitting in-flight edits). Fixes #21.
  — goosed binary: `vendor/goosed-linux-amd64.sha256`

---

## [0.9.0] — 2026-05-27

### Added
- Runbook 0003: DeepSeek `reasoning_content` 400 backfill fix (post-Phase-9 regression)
- `bin/lina-log` — structured log viewer
- Session retrospective: 2026-05-27 (performance 7.5/10, intelligence 8.5/10)

### Fixed
- **Empty thinking bubble** (`Bad Request: message text is empty`) — added empty-HTML guard
  in `gateway/telegram.rs` (`edit_text`).
- **DeepSeek 400 on multi-turn** (`reasoning_content must be passed back`) — defensive
  backfill in `providers/openai.rs` `sanitize_request_for_compat()`.
- **Code block truncation** — `smart_args_preview` capped to 120 chars in `gateway/handler.rs`.
- Restored DeepSeek thinking mode after inadvertent disabling in Phase 8 (Runbook 0002).

---

## [0.8.0] — 2026-05-26

### Added
- MCP `lina-moodle`: full Moodle LMS integration (login, courses, quizzes, answers)
- MCP `lina-systemd-user`: manage user systemd services via LINA
- `bin/lina-doctor` — health check script
- `config/mcp-registry.yaml` — MCP registry with wave-based deployment plan
- `justfile` — full lifecycle automation (bootstrap, secrets-init, apply-config, start/stop)
- `AGENTS.md` — Goose agent conventions for this workspace

### Changed
- DeepSeek provider patched to support custom `GOOSE_PROVIDER=custom_deepseek`
- `GOOSE_GATEWAY_MAX_TURNS` raised to 200 (requires patched goosed)

---

## [0.7.0] — 2026-05-25

### Added
- Core MCP Wave 1: `lina-secrets`, `lina-fs-safe`, `lina-shell-policy`
- Telegram gateway integration with Goose
- Voice note transcription via `whisper-ctranslate2` (large-v3-turbo, CUDA)
- systemd user service `lina-goosed.service`
- ADR 0001: Clean Architecture for MCP servers
- Runbook 0001: DeepSeek V4 reasoning bug fix

### Changed
- Goose built from source with `code-mode,system-keyring,portable-default` features

---

[Unreleased]: https://github.com/<your-user>/lina/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/<your-user>/lina/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/<your-user>/lina/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/<your-user>/lina/releases/tag/v0.7.0

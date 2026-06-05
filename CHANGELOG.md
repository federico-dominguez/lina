# Changelog

All notable changes to LINA are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Versioning follows [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`.

> **Note:** Prior to v1.0.0, LINA was developed as a personal unversioned project.
> This changelog records the state at initial repository publication and going forward.

---

## [Unreleased]

### Added
- **Phase 1: Protocolo Conversacional** (#166)
  - `FloorTokenManager` en `floor.py`: adquisición, renovación, liberación y timeout de turno conversacional
  - Cola FIFO de mensajes entre bots: `enqueue_message()`, `ack_message()`, `get_pending_messages()`
  - Contexto acumulativo: `get_context_messages()` inyecta últimos N mensajes en prompts
  - Turn-timeout escalado por bot: LINA=60s, Cline=120s, Goose=45s, Gemma=60s
  - 15 unit tests para FloorTokenManager (modo bypass sin DB)
- **Migration tracking system**: tabla `schema_migrations`, script `sql/migrations/_migrate.sh`, y comando `just migrate` para aplicar migraciones SQL de forma ordenada y reproducible (#164)
- **Audit schema (Fase 3 hardening) aplicado**: migración 001 ejecutada en instancia local. Schema `audit` con `audit.tool_calls`, vista `audit.daily_summary`, y vista de compatibilidad `public.audit_logs`. Herramientas `get_audit_logs()` y `get_daily_summary()` del MCP `lina-db` ahora funcionales (#164)

### Added
- **Fase 3: Hardening a nivel producción** (Issue #11)
  - `x-mcp-defaults` YAML anchor en `docker-compose.yml`: todos los MCPs heredan
    `read_only: true`, `cap_drop: ALL`, `security_opt: no-new-privileges`, `tmpfs: /tmp`,
    logging JSON con rotación. Agregar un MCP nuevo hereda la postura completa.
  - `deploy/docker/nginx/nginx.conf` + servicio `lina-mcp-gateway` (nginx:1.27-alpine):
    proxy reverso para los 6 MCPs en puertos 8101–8106 (localhost). Los MCPs dejan de
    exponer puertos directamente; nginx es el único punto de entrada.
    Rate limits: shell-policy 30 req/min (burst 5), moodle 20 req/min (burst 5),
    resto 120 req/min (burst 20). HTTP 429 en exceso.
  - `sql/init/002-audit-schema.sql`: schema PostgreSQL `audit` con tabla `audit.tool_calls`
    (agrega columnas `mcp` y `duration_ms`) y vista `audit.daily_summary` (resumen diario
    de uso por MCP/tool). Vista de compatibilidad `public.audit_logs`.
  - `sql/migrations/001-audit-schema.sql`: migración idempotente para instancias existentes.
  - `lina-db` MCP: herramienta `get_daily_summary(days)` — consulta `audit.daily_summary`.
    `_audit()` ahora escribe en `audit.tool_calls`.
  - `deploy/docker/backup/backup.sh` + servicio `lina-backup` (postgres:16-alpine):
    `pg_dump` horario comprimido con gzip-9. Retención configurable via `$BACKUP_KEEP`
    (default 24 copias = ~1 día). Volumen `lina-backup-data`.
  - `Dockerfile.mcp`: `ENV PYTHONDONTWRITEBYTECODE=1` — evita escrituras de .pyc al FS
    read-only del contenedor.
  - `docs/architecture/0009-fase-3-hardening.md`: ADR con todas las decisiones de Fase 3.
  - `docs/runbooks/0004-recovery.md`: runbook de recovery — reinicio del host, MCP caído,
    restore desde backup, reset completo.

### Changed
- `deploy/docker/docker-compose.yml`: MCPs refactorizados con `x-mcp-defaults`;
  `lina-mcp-db` agrega `depends_on: lina-db: condition: service_healthy`;
  logging JSON-file en todos los servicios.
- `tests/integration/mcps/test_mcp_containers.sh`: el smoke test levanta `lina-mcp-gateway`
  junto a los MCPs (necesario ya que los puertos se exponen vía nginx).

### Added
- **Fase 2: Containerización completa de MCPs** (Issue #10)
  - Nuevo `deploy/docker/Dockerfile.mcp`: Dockerfile multi-stage compartido para todos los
    MCPs, parametrizado con `ARG MCP_DIR` y `ARG MCP_CMD`. Stage builder usa `uv sync --no-dev`;
    stage runtime corre como usuario no-root `mcp` (uid 1001). Healthcheck TCP vía Python3
    (FastMCP no expone `/health`). `ENV MCP_TRANSPORT=sse MCP_PORT=8000` por defecto.
  - `deploy/docker/docker-compose.yml`: 6 nuevos servicios MCP en puertos 8101–8106:
    - `lina-mcp-secrets` (8101): backend de archivos (`LINA_SECRETS_BACKEND=file`),
      volumen `lina-secrets-data` en `/run/secrets/lina`.
    - `lina-mcp-fs-safe` (8102): monta `$HOME` del host.
    - `lina-mcp-shell-policy` (8103): hardened — `read_only: true`, `tmpfs: [/tmp:size=64m]`,
      `cap_drop: ALL`, audit log en tmpfs (`LINA_SHELL_AUDIT_DIR=/tmp/lina-audit`).
    - `lina-mcp-systemd-user` (8104): socket D-Bus del usuario host montado en
      `/run/user/1000/bus` con `DBUS_SESSION_BUS_ADDRESS`.
    - `lina-mcp-moodle` (8105): red aislada `lina-moodle-net`.
    - `lina-mcp-db` (8106): depends_on `lina-db` (PostgreSQL).
  - `lina-secrets`: backend de archivos (`LINA_SECRETS_BACKEND=file | keyring`).
    Las operaciones `secret_get/set/delete/list` ramifican según el backend. Compatible
    hacia atrás — en modo stdio el default sigue siendo `keyring`. Abandona libsecret
    en containers (no funciona sin D-Bus de sesión).
  - `lina-shell-policy`: `LINA_SHELL_AUDIT_DIR` configurable desde entorno (antes hardcodeado
    a `~/lina/logs`).
  - Soporte `MCP_TRANSPORT` / `MCP_PORT` en 4 MCPs que aún no lo tenían:
    `lina-secrets`, `lina-shell-policy`, `lina-systemd-user`, `lina-moodle`.
    (`lina-db` y `lina-fs-safe` ya lo tenían desde Fase 1.)
  - `config/mcp-registry.yaml`: campos `container_url` y `container_port` por MCP.
  - `tests/integration/mcps/test_mcp_containers.sh`: smoke test que levanta los
    6 contenedores, verifica TCP y endpoint SSE en cada puerto.
- ADR 0003: Android control via ADB bridge (hybrid architecture, MCP `lina-android-remote` planned)
- ADR 0002: UI rendering strategy (Telegram HTML rendering)

### Changed
- Repository migrated to private GitHub with full professional structure
- Questionnaire output files moved to `~/Documentos/lina-moodle-exports/`

### Fixed
- **Telegram final message truncated at 4096 chars** — patched `goosed` binary now splits
  responses that exceed 4096 characters into multiple consecutive messages instead of
  silently dropping the remainder with `…`. In-flight live-edit bubbles retain safe truncation
  (Telegram does not allow splitting an in-progress edit). Source change is in the local
  Goose fork (`crates/goose/src/gateway/telegram.rs`, function `edit_text`); this repo
  tracks the compiled binary checksum at `vendor/goosed-linux-amd64.sha256`. Fixes #21.

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

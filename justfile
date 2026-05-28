set shell := ["bash", "-euo", "pipefail", "-c"]
set dotenv-load := false

HOME_DIR        := env_var('HOME')
LINA_DIR        := HOME_DIR / "lina"
GOOSE_CFG_DIR   := HOME_DIR / ".config/goose"
SECRETS_FILE    := GOOSE_CFG_DIR / "secrets.env"
CONFIG_TMPL     := LINA_DIR / "config/goose.config.yaml.tmpl"
CONFIG_TARGET   := GOOSE_CFG_DIR / "config.yaml"
UNIT_SRC        := LINA_DIR / "deploy/systemd/lina-goosed.service"
UNIT_DST        := HOME_DIR / ".config/systemd/user/lina-goosed.service"
GOOSED_SRC      := HOME_DIR / "goosed_patched"
GOOSED_LINK     := LINA_DIR / "bin/goosed"

default:
    @just --list

# ─── Bootstrap ────────────────────────────────────────────────────────────────
bootstrap: _check-prereqs _install-uv _symlink-goosed _install-mcps _install-unit
    @echo ""
    @echo "✅ Bootstrap completo."
    @echo "   Siguiente paso:  just secrets-init  &&  just apply-config  &&  just start"

_check-prereqs:
    @command -v curl  >/dev/null || { echo "❌ falta curl";  exit 1; }
    @command -v just  >/dev/null || { echo "❌ falta just (cargo install just)"; exit 1; }
    @command -v systemctl >/dev/null || { echo "❌ falta systemctl"; exit 1; }
    @test -x "{{GOOSED_SRC}}" || { echo "❌ no existe {{GOOSED_SRC}}"; exit 1; }
    @echo "✓ prereqs OK"

_install-uv:
    @if ! command -v uv >/dev/null; then \
        echo "→ instalando uv"; \
        curl -LsSf https://astral.sh/uv/install.sh | sh ; \
    else echo "✓ uv presente"; fi

_symlink-goosed:
    @mkdir -p "{{LINA_DIR}}/bin"
    @ln -sfn "{{GOOSED_SRC}}" "{{GOOSED_LINK}}"
    @echo "✓ {{GOOSED_LINK}} → {{GOOSED_SRC}}"

_install-mcps:
    @for d in secrets fs-safe shell-policy systemd-user; do \
        echo "→ uv sync mcps/$$d"; \
        (cd "{{LINA_DIR}}/mcps/$$d" && uv sync --quiet) ; \
    done

_install-unit:
    @mkdir -p "$(dirname "{{UNIT_DST}}")"
    @install -m 0644 "{{UNIT_SRC}}" "{{UNIT_DST}}"
    @systemctl --user daemon-reload
    @loginctl enable-linger "$USER" 2>/dev/null || true
    @echo "✓ systemd unit instalada en {{UNIT_DST}}"

# ─── Secretos ─────────────────────────────────────────────────────────────────
secrets-init:
    @mkdir -p "{{GOOSE_CFG_DIR}}"
    @if [[ -f "{{SECRETS_FILE}}" ]]; then \
        echo "⚠ {{SECRETS_FILE}} ya existe — abortando para no sobrescribir."; exit 1; fi
    @install -m 0600 /dev/null "{{SECRETS_FILE}}"
    @read -rp "DEEPSEEK_API_KEY: " DK; \
     read -rp "TELEGRAM_BOT_TOKEN: " TT; \
     { echo "DEEPSEEK_API_KEY=$$DK"; \
       echo "TELEGRAM_BOT_TOKEN=$$TT"; \
       grep -vE '^(DEEPSEEK_API_KEY|TELEGRAM_BOT_TOKEN)=' "{{LINA_DIR}}/.env.example"; \
     } > "{{SECRETS_FILE}}"
    @chmod 600 "{{SECRETS_FILE}}"
    @echo "✓ secrets escritos en {{SECRETS_FILE}} (chmod 600)"
    @echo "   migra MOODLE_PASSWORD y otros con:  just secrets-set moodle MOODLE_PASSWORD"

# Guarda una credencial en el system-keyring vía el MCP secrets.
# uso: just secrets-set <service> <key>
secrets-set service key:
    @cd "{{LINA_DIR}}/mcps/secrets" && uv run python -m lina_secrets.cli set {{service}} {{key}}

secrets-get service key:
    @cd "{{LINA_DIR}}/mcps/secrets" && uv run python -m lina_secrets.cli get {{service}} {{key}}

# ─── Config render ────────────────────────────────────────────────────────────
apply-config:
    @test -f "{{SECRETS_FILE}}" || { echo "❌ corré 'just secrets-init' primero"; exit 1; }
    @set -a; source "{{SECRETS_FILE}}"; set +a; \
     envsubst < "{{CONFIG_TMPL}}" > "{{CONFIG_TARGET}}.new"
    @# Preservar gateway_pairings y gateway_pending_codes del config actual.
    @uv run --with pyyaml "{{LINA_DIR}}/bin/merge-config.py" "{{CONFIG_TARGET}}.new" "{{CONFIG_TARGET}}"
    @if [[ -f "{{CONFIG_TARGET}}" ]]; then \
        cp -a "{{CONFIG_TARGET}}" "{{CONFIG_TARGET}}.bak.$(date +%s)"; fi
    @mv "{{CONFIG_TARGET}}.new" "{{CONFIG_TARGET}}"
    @chmod 600 "{{CONFIG_TARGET}}"
    @echo "✓ {{CONFIG_TARGET}} regenerado (backup .bak.<ts>)"

# ─── Lifecycle ────────────────────────────────────────────────────────────────
start:
    systemctl --user start lina-goosed.service
    @sleep 1 && just status

stop:
    systemctl --user stop lina-goosed.service

restart:
    systemctl --user restart lina-goosed.service
    @sleep 1 && just status

status:
    @systemctl --user status lina-goosed.service --no-pager -l | head -20 || true
    @echo "---"
    @goose gateway status 2>&1 || true

logs:
    journalctl --user -fu lina-goosed.service

logs-tail n="200":
    journalctl --user -u lina-goosed.service -n {{n}} --no-pager

# ─── Tests ───────────────────────────────────────────────────────────────────
test:
    @for d in secrets fs-safe shell-policy systemd-user moodle; do \
        if [[ -d "{{LINA_DIR}}/mcps/$$d/tests" ]]; then \
            echo "→ pytest mcps/$$d"; \
            (cd "{{LINA_DIR}}/mcps/$$d" && uv run pytest tests/ -v --tb=short) ; \
        else echo "⚠ mcps/$$d: no tests/ dir yet"; fi \
    done

test-integration:
    @echo "→ integration tests (requires running LINA environment)"
    @uv run --project "{{LINA_DIR}}" pytest "{{LINA_DIR}}/tests/integration/" -v --tb=short 2>/dev/null || echo "No integration tests yet"

# ─── Lint ─────────────────────────────────────────────────────────────────────
lint:
    @for d in secrets fs-safe shell-policy systemd-user moodle; do \
        echo "→ ruff mcps/$$d"; \
        (cd "{{LINA_DIR}}/mcps/$$d" && uv run ruff check src/ && uv run ruff format --check src/) || true ; \
    done

# ─── Health ───────────────────────────────────────────────────────────────────
doctor:
    @"{{LINA_DIR}}/bin/lina-doctor"

#!/bin/bash
# Goose standalone gateway — conecta @s_goose_bot al goosed local (port 42359)
#
# Los tokens se cargan desde ~/.config/goose/secrets.env (chmod 600)
set -e

# Cargar secrets
SECRETS_FILE="${HOME}/.config/goose/secrets.env"
if [ -f "$SECRETS_FILE" ]; then
    set -a
    source "$SECRETS_FILE"
    set +a
fi

cd /home/fede/lina/infrastructure/gateway/telegram

# Export Goose-specific env vars (con fallback a secrets cargados)
export MULTI_BOT_COUNT=1
export BOT_1_NAME="${BOT_1_NAME:-Goose}"
export BOT_1_USERNAME="${BOT_1_USERNAME:-s_goose_bot}"
export BOT_1_TOKEN="${GOOSE_BOT_TOKEN:?GOOSE_BOT_TOKEN no está en secrets.env}"
export BOT_1_GOOSED_URL="${BOT_1_GOOSED_URL:-https://localhost:42359}"
export BOT_1_GOOSED_SECRET="${BOT_1_GOOSED_SECRET:-${GOOSE_SERVER__SECRET_KEY:?GOOSE_SERVER__SECRET_KEY no está en secrets.env}}"
export BOT_1_TRUSTED_USERS="${GOOSE_BOT_TRUSTED_USERS:-telegram:7966401870,telegram:5110614353}"
export BOT_1_NOTIFY_CHAT_IDS="${BOT_1_NOTIFY_CHAT_IDS:-}"
export BOT_1_OBSERVE_PORT="${BOT_1_OBSERVE_PORT:-9096}"
export LINA_DB_URL="${LINA_DB_URL:-postgresql://lina:lina_dev@localhost:5432/lana}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY no está en secrets.env}"

exec .venv/bin/python /home/fede/lina/bin/lina-gateway-entrypoint.py

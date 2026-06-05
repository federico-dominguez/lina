#!/bin/bash
# LINA Gateway Multi-Bot: LINA + Cline + Gemma
# Goose tiene su propio gateway separado (goose-gateway-wrapper.sh)
#
# Los tokens se cargan desde ~/.config/goose/secrets.env (chmod 600)
set -e

# Cargar secrets (con defaults seguros)
SECRETS_FILE="${HOME}/.config/goose/secrets.env"
if [ -f "$SECRETS_FILE" ]; then
    set -a
    source "$SECRETS_FILE"
    set +a
fi

cd /home/fede/lina/infrastructure/gateway/telegram

# Export all env vars (source may have already done this, but be explicit)
export MULTI_BOT_COUNT="${MULTI_BOT_COUNT:-3}"
export BOT_1_NAME="${BOT_1_NAME:-LINA}"
export BOT_1_USERNAME="${BOT_1_USERNAME:-s_lina_bot}"
export BOT_1_TOKEN="${LINA_BOT_TOKEN:?LINA_BOT_TOKEN no está en secrets.env}"
export BOT_1_GOOSED_URL="${BOT_1_GOOSED_URL:-https://localhost:3000}"
export BOT_1_GOOSED_SECRET="${LINA_GOOSED_SECRET:-${GOOSE_SERVER__SECRET_KEY:-}}"
export BOT_1_TRUSTED_USERS="${BOT_1_TRUSTED_USERS:-telegram:7966401870,telegram:5110614353}"
export BOT_1_NOTIFY_CHAT_IDS="${BOT_1_NOTIFY_CHAT_IDS:-telegram:7966401870}"
export BOT_1_OBSERVE_PORT="${BOT_1_OBSERVE_PORT:-9093}"
export BOT_1_FLOOR_TIMEOUT="${BOT_1_FLOOR_TIMEOUT:-60}"
export BOT_2_NAME="${BOT_2_NAME:-Cline}"
export BOT_2_USERNAME="${BOT_2_USERNAME:-s_cline_bot}"
export BOT_2_TOKEN="${CLINE_BOT_TOKEN:?CLINE_BOT_TOKEN no está en secrets.env}"
export BOT_2_GOOSED_URL="${BOT_2_GOOSED_URL:-https://localhost:3001}"
export BOT_2_GOOSED_SECRET="${CLINE_GOOSED_SECRET:-${GOOSE_SERVER__SECRET_KEY:-}}"
export BOT_2_TRUSTED_USERS="${BOT_2_TRUSTED_USERS:-telegram:7966401870,telegram:5110614353}"
export BOT_2_NOTIFY_CHAT_IDS="${BOT_2_NOTIFY_CHAT_IDS:-telegram:7966401870}"
export BOT_2_OBSERVE_PORT="${BOT_2_OBSERVE_PORT:-9092}"
export BOT_2_FLOOR_TIMEOUT="${BOT_2_FLOOR_TIMEOUT:-120}"
export BOT_3_NAME="${BOT_3_NAME:-Gemma}"
export BOT_3_USERNAME="${BOT_3_USERNAME:-s_gemma_bot}"
export BOT_3_TOKEN="${GEMMA_BOT_TOKEN:?GEMMA_BOT_TOKEN no está en secrets.env}"
export BOT_3_GOOSED_URL="${BOT_3_GOOSED_URL:-https://localhost:3002}"
export BOT_3_GOOSED_SECRET="${GEMMA_GOOSED_SECRET:-}"
export BOT_3_TRUSTED_USERS="${BOT_3_TRUSTED_USERS:-telegram:7966401870,telegram:5110614353}"
export BOT_3_NOTIFY_CHAT_IDS="${BOT_3_NOTIFY_CHAT_IDS:-telegram:7966401870}"
export BOT_3_OBSERVE_PORT="${BOT_3_OBSERVE_PORT:-9094}"
export BOT_3_FLOOR_TIMEOUT="${BOT_3_FLOOR_TIMEOUT:-60}"
export LINA_DB_URL="${LINA_DB_URL:-postgresql://lina:lina_dev@localhost:5432/lina}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY no está en secrets.env}"

exec .venv/bin/python /home/fede/lina/bin/lina-gateway-entrypoint.py

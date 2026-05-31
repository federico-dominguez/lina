#!/usr/bin/env bash
# lina-watchdog.sh — monitors goosed healthcheck and restarts via Docker Compose.
#
# Runs as a systemd --user service on the host (outside Docker).
# On 3 consecutive unhealthy checks: restarts the goosed container and notifies
# Federico via Telegram Bot API.
#
# Required env vars (loaded from EnvironmentFile in the .service unit):
#   TELEGRAM_BOT_TOKEN     — Telegram bot token
#   TELEGRAM_CHAT_ID       — Telegram chat ID to notify (Federico's)
#
# Optional env vars (with defaults):
#   COMPOSE_FILE           — path to docker-compose.yml
#   HEALTHCHECK_INTERVAL   — seconds between checks (default: 30)
#   MAX_FAILURES           — consecutive failures before restart (default: 3)

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-$HOME/lina/deploy/docker/docker-compose.yml}"
# Container name follows Docker Compose project naming: <project>-<service>-<n>.
# With 'cd ~/lina && docker compose …' the project name is 'lina',
# so the container is 'lina-goosed-1'.
CONTAINER_NAME="${CONTAINER_NAME:-lina-goosed-1}"
HEALTHCHECK_INTERVAL="${HEALTHCHECK_INTERVAL:-30}"
MAX_FAILURES="${MAX_FAILURES:-3}"

fail_count=0

# ─── helpers ──────────────────────────────────────────────────────────────────

log() {
    echo "$(date -u +%FT%T.%3NZ) [lina-watchdog] $*"
}

tg_notify() {
    local msg="$1"
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
        curl -sf -X POST \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "parse_mode=HTML" \
            --data-urlencode "text=${msg}" \
            > /dev/null 2>&1 || true
    fi
}

container_health() {
    docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_NAME}" 2>/dev/null \
        || echo "error"
}

# ─── main loop ────────────────────────────────────────────────────────────────

log "Starting (interval=${HEALTHCHECK_INTERVAL}s, max_failures=${MAX_FAILURES}, container=${CONTAINER_NAME})"
tg_notify "🐕 <b>Watchdog iniciado.</b> Monitoreando ${CONTAINER_NAME} cada ${HEALTHCHECK_INTERVAL}s."

while true; do
    sleep "${HEALTHCHECK_INTERVAL}"

    status=$(container_health)

    if [[ "$status" == "healthy" ]]; then
        if [[ $fail_count -gt 0 ]]; then
            log "goosed recovered after $fail_count failure(s)"
            tg_notify "✅ <b>goosed recuperado</b> tras ${fail_count} chequeo(s) fallido(s)."
        fi
        fail_count=0
        continue
    fi

    fail_count=$((fail_count + 1))
    log "goosed unhealthy (status=${status}), failure ${fail_count}/${MAX_FAILURES}"

    if [[ $fail_count -ge $MAX_FAILURES ]]; then
        log "Triggering restart of ${CONTAINER_NAME}..."
        tg_notify "⚠️ <b>goosed caído</b> (${fail_count} chequeos fallidos). Reiniciando..."

        if docker compose -f "${COMPOSE_FILE}" restart goosed 2>&1 | while IFS= read -r line; do
            log "compose: $line"
        done; then
            log "Restart command completed successfully."
        else
            log "WARNING: restart command exited with error."
            tg_notify "❌ <b>Error al reiniciar goosed.</b> Revisá el host manualmente."
        fi

        fail_count=0
    fi
done

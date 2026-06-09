#!/usr/bin/env bash
# /usr/local/bin/lina-deploy
#
# Wrapper de despliegue para LINA.
# Permite que LINA reinicie/rebuild sus propios contenedores vía sudo sin
# necesitar acceso completo al Docker daemon.
#
# Instalación:
#   sudo install -m 755 -o root -g root deploy/lina-deploy.sh /usr/local/bin/lina-deploy
#
# Uso:
#   sudo lina-deploy restart <servicio>
#   sudo lina-deploy rebuild <servicio>
#   sudo lina-deploy stop <servicio>
#   sudo lina-deploy start <servicio>
#   sudo lina-deploy status
#
# Seguridad:
#   - Solo actúa sobre servicios en ALLOWED_SERVICES.
#   - Siempre opera desde el directorio del compose (COMPOSE_FILE).
#   - Los comandos permitidos (ALLOWED_CMDS) están enumerados explícitamente.
#   - Registra cada invocación en /var/log/lina-deploy.log.

set -euo pipefail

# Ruta al compose file. Puede sobreescribirse via variable de entorno.
# Por defecto se computa relativo al script, lo que lo hace portable.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${LINA_COMPOSE_FILE:-${SCRIPT_DIR}/docker/docker-compose.yml}"
LOG_FILE="/var/log/lina-deploy.log"

ALLOWED_SERVICES=(
    goosed
    lina-gateway
    lina-mcp-shell-policy
    lina-mcp-secrets
    lina-mcp-fs-safe
    lina-mcp-github
    lina-mcp-gitlab
    lina-mcp-gcalendar
    lina-mcp-systemd-user
    lina-mcp-moodle
    lina-mcp-db
    lina-mcp-gns3
    lina-mcp-gateway
    lina-docker-proxy
)

ALLOWED_CMDS=(restart rebuild stop start status)

# ── helpers ──────────────────────────────────────────────────────────────────

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] lina-deploy: $*" | tee -a "$LOG_FILE" >&2
}

is_allowed_service() {
    local svc="$1"
    for allowed in "${ALLOWED_SERVICES[@]}"; do
        [[ "$svc" == "$allowed" ]] && return 0
    done
    return 1
}

is_allowed_cmd() {
    local cmd="$1"
    for allowed in "${ALLOWED_CMDS[@]}"; do
        [[ "$cmd" == "$allowed" ]] && return 0
    done
    return 1
}

usage() {
    echo "Uso: lina-deploy <comando> [servicio]"
    echo "Comandos: ${ALLOWED_CMDS[*]}"
    echo "Servicios: ${ALLOWED_SERVICES[*]}"
    exit 1
}

# ── main ──────────────────────────────────────────────────────────────────────

[[ $# -lt 1 ]] && usage

CMD="$1"
SVC="${2:-}"

if ! is_allowed_cmd "$CMD"; then
    log "DENIED: comando '${CMD}' no está en la allowlist"
    echo "Error: comando '${CMD}' no permitido." >&2
    exit 2
fi

if [[ "$CMD" == "status" ]]; then
    log "status (all services)"
    docker compose -f "$COMPOSE_FILE" ps
    exit 0
fi

if [[ -z "$SVC" ]]; then
    echo "Error: se requiere nombre de servicio para '$CMD'." >&2
    usage
fi

if ! is_allowed_service "$SVC"; then
    log "DENIED: servicio '${SVC}' no está en la allowlist"
    echo "Error: servicio '${SVC}' no permitido." >&2
    exit 2
fi

log "${CMD} ${SVC}"

case "$CMD" in
    restart)
        docker compose -f "$COMPOSE_FILE" restart "$SVC"
        ;;
    rebuild)
        docker compose -f "$COMPOSE_FILE" up -d --build "$SVC"
        ;;
    stop)
        docker compose -f "$COMPOSE_FILE" stop "$SVC"
        ;;
    start)
        docker compose -f "$COMPOSE_FILE" start "$SVC"
        ;;
esac

log "${CMD} ${SVC}: done"

#!/usr/bin/env bash
# tests/integration/mcps/test_mcp_containers.sh
#
# Smoke test: levanta los contenedores MCP, verifica TCP en cada puerto y
# comprueba que el endpoint /sse responde 200 OK.
#
# Requisitos:
#   - docker compose disponible
#   - Los MCPs de lina-db y lina-moodle necesitan vars de entorno (ver .env.example)
#
# Uso:
#   bash tests/integration/mcps/test_mcp_containers.sh
#
# Exit codes:
#   0 — todos los contenedores healthy
#   1 — uno o más contenedores no pasaron el smoke test

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/docker/docker-compose.yml"
# Project name dedicado para no interferir con el stack normal ni entre runs
COMPOSE_PROJECT="lina-smoke-test"
COMPOSE=(docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT")

# Puerto → nombre del servicio
declare -A MCP_PORTS=(
  [8101]="lina-mcp-secrets"
  [8102]="lina-mcp-fs-safe"
  [8103]="lina-mcp-shell-policy"
  [8104]="lina-mcp-systemd-user"
  [8105]="lina-mcp-moodle"
  [8106]="lina-mcp-db"
)

WAIT_SECS=30
FAILED=0

log()  { echo "[test_mcp_containers] $*" >&2; }
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; FAILED=1; }

# ─── Cleanup al salir (éxito o fallo) ────────────────────────────────────────
_cleanup() {
  log "Limpiando contenedores del proyecto $COMPOSE_PROJECT..."
  "${COMPOSE[@]}" down --remove-orphans --volumes 2>/dev/null || true
}
trap _cleanup EXIT

# ─── Levantar contenedores MCP ───────────────────────────────────────────────
log "Levantando contenedores MCP (docker compose up -d)..."
"${COMPOSE[@]}" up -d \
  lina-mcp-secrets lina-mcp-fs-safe lina-mcp-shell-policy \
  lina-mcp-systemd-user lina-mcp-moodle lina-mcp-db

# ─── Esperar healthchecks ─────────────────────────────────────────────────────
log "Esperando ${WAIT_SECS}s para que los contenedores lleguen a 'healthy'..."
sleep "$WAIT_SECS"

# ─── Verificar TCP en cada puerto ─────────────────────────────────────────────
log "Verificando conectividad TCP..."
for port in "${!MCP_PORTS[@]}"; do
  svc="${MCP_PORTS[$port]}"
  if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
r = s.connect_ex(('127.0.0.1', $port))
s.close()
sys.exit(r)
" 2>/dev/null; then
    pass "TCP puerto $port ($svc) — OK"
  else
    fail "TCP puerto $port ($svc) — NO RESPONDE"
  fi
done

# ─── Verificar endpoint SSE ───────────────────────────────────────────────────
# Usamos HEAD o bien abrimos una conexión TCP y leemos solo la línea de status
# para evitar que SSE deje la conexión abierta y curl espere para siempre.
log "Verificando endpoint SSE (HTTP 200/primera línea)..."
for port in "${!MCP_PORTS[@]}"; do
  svc="${MCP_PORTS[$port]}"
  # Leer solo la línea de status HTTP; cerrar la conexión inmediatamente.
  status_line=$(python3 -c "
import socket, sys
try:
    s = socket.socket()
    s.settimeout(5)
    s.connect(('127.0.0.1', $port))
    s.sendall(b'GET /sse HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n')
    line = s.recv(256).decode(errors='replace').split('\\r\\n')[0]
    s.close()
    print(line)
except Exception as e:
    print(f'ERROR: {e}')
" 2>/dev/null)
  if echo "$status_line" | grep -q '^HTTP/.* 200'; then
    pass "HTTP /sse :$port ($svc) — OK ($status_line)"
  else
    fail "HTTP /sse :$port ($svc) — respuesta: '$status_line'"
  fi
done

# ─── Resultado ───────────────────────────────────────────────────────────────
echo ""
if [[ "$FAILED" -eq 0 ]]; then
  log "Todos los contenedores MCP pasaron el smoke test ✓"
  exit 0
else
  log "Uno o más contenedores fallaron. Ver salida arriba."
  "${COMPOSE[@]}" ps
  exit 1
fi

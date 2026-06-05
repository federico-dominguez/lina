#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# lina-health-check — Monitoreo de contenedores + alerta temprana de OOM
#
# Uso:
#   ./bin/lina-health-check.sh                 # chequeo único
#   ./bin/lina-health-check.sh --watch         # loop cada 30s
#   ./bin/lina-health-check.sh --install       # instalar como systemd timer
#
# Alertas: escribe a syslog cuando detecta contenedores usando >80% de su
# memory limit o >1 GB en contenedores sin límite explícito.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_NAME="lina-health-check"

# ── Config ──────────────────────────────────────────────────────────────────
MEM_WARN_THRESHOLD_PCT=80          # % del memory limit → warning
MEM_WARN_ABSOLUTE_MB=1024          # MB absolutos para contenedores sin límite
LOOP_INTERVAL=30                   # segundos entre checks en --watch
ALERT_LOG="/var/log/lina-health-check.log"
ALERT_TMP="/tmp/lina-oom-alert.lock"  # evita spam de alertas (cooldown 5 min)

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── Helpers ─────────────────────────────────────────────────────────────────
log()   { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${CYAN}[${SCRIPT_NAME}]${NC} $*"; }
warn()  { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${RED}[ERROR]${NC} $*"; }
ok()    { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${GREEN}[OK]${NC}    $*"; }

alert() {
  local msg="$1"
  warn "$msg"
  logger -t "${SCRIPT_NAME}" "ALERT: ${msg}"
  echo "$(date '+%Y-%m-%d %H:%M:%S') ALERT: ${msg}" >> "${ALERT_LOG}"
}

# ── Checks ──────────────────────────────────────────────────────────────────

check_cpu_temp() {
  local temp
  if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    temp=$(($(cat /sys/class/thermal/thermal_zone0/temp) / 1000))
    if [ "$temp" -gt 85 ]; then
      error "🔥 CPU temperature CRITICAL: ${temp}°C"
    elif [ "$temp" -gt 70 ]; then
      warn "🌡️ CPU temperature high: ${temp}°C"
    else
      ok "CPU temperature: ${temp}°C"
    fi
  fi
}

check_host_memory() {
  local total_mem_kb used_mem_kb pct
  total_mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
  used_mem_kb=$(( total_mem_kb - $(awk '/MemAvailable/ {print $2}' /proc/meminfo) ))
  pct=$(( used_mem_kb * 100 / total_mem_kb ))

  echo -e "  Host RAM: $(echo "scale=1; ${used_mem_kb}/1048576" | bc)G / $(echo "scale=1; ${total_mem_kb}/1048576" | bc)G (${pct}%)"

  if [ "$pct" -gt 90 ]; then
    alert "⚠️ HOST RAM CRITICAL: ${pct}% used — OOM risk!"
  elif [ "$pct" -gt 80 ]; then
    warn "⚠️ Host RAM high: ${pct}% used"
  fi
}

check_containers() {
  local alerts=0
  local errors=0
  local running=0
  local stopped=0
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  CONTAINER HEALTH CHECK"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  # Header for the table
  printf "  %-30s %-12s %-16s %s\n" "NAME" "STATUS" "MEM USAGE" "MEM LIMIT"
  echo "  ─────────────────────────────────────────────────────────────"
  
  while IFS=$'\t' read -r name status mem_used mem_limit cpu_pct restart_count; do
    # Parse status
    case "$status" in
      *"Up"*)
        running=$((running + 1))
        status_display="${GREEN}UP${NC}"
        ;;
      *"Restarting"*)
        errors=$((errors + 1))
        status_display="${RED}RESTARTING${NC}"
        warn "⚠️ Container ${name} is restarting (count: ${restart_count:-?})"
        ;;
      *"Exited"*)
        stopped=$((stopped + 1))
        if echo "$status" | grep -q "(0)"; then
          status_display="${CYAN}EXITED(0)${NC}"
        else
          errors=$((errors + 1))
          status_display="${RED}EXITED${NC}"
          error "💀 Container ${name} exited with error: ${status}"
        fi
        ;;
      *)
        status_display="${YELLOW}UNKNOWN${NC}"
        ;;
    esac
    
    # Parse memory
    local mem_usage_mb=0
    local mem_limit_mb=0
    
    if [[ "$mem_used" =~ ^([0-9.]+)(MiB|GiB)$ ]]; then
      if [[ "${BASH_REMATCH[2]}" == "GiB" ]]; then
        mem_usage_mb=$(echo "${BASH_REMATCH[1]} * 1024" | bc)
      else
        mem_usage_mb="${BASH_REMATCH[1]}"
      fi
    fi
    
    if [[ "$mem_limit" =~ ^([0-9.]+)(MiB|GiB)$ ]]; then
      if [[ "${BASH_REMATCH[2]}" == "GiB" ]]; then
        mem_limit_mb=$(echo "${BASH_REMATCH[1]} * 1024" | bc)
      else
        mem_limit_mb="${BASH_REMATCH[1]}"
      fi
    fi
    
    # Format memory display
    local mem_display mem_limit_display
    if [ "$(echo "$mem_usage_mb > 1024" | bc -l 2>/dev/null)" = "1" ]; then
      mem_display="$(echo "scale=1; $mem_usage_mb / 1024" | bc)G"
    else
      mem_display="${mem_usage_mb%.*}M"
    fi
    
    if [ "$(echo "$mem_limit_mb > 0" | bc -l 2>/dev/null)" = "1" ]; then
      if [ "$(echo "$mem_limit_mb > 1024" | bc -l 2>/dev/null)" = "1" ]; then
        mem_limit_display="$(echo "scale=1; $mem_limit_mb / 1024" | bc)G"
      else
        mem_limit_display="${mem_limit_mb%.*}M"
      fi
    else
      mem_limit_display="unlimited"
    fi
    
    printf "  %-30s %-12s %-16s %s\n" "${name}" "${status_display}" "${mem_display}" "${mem_limit_display}"
    
    # ── OOM detection ───────────────────────────────────────────────
    # Check if memory usage exceeds threshold
    local warn_flag=0
    if [ "$(echo "$mem_limit_mb > 0" | bc -l 2>/dev/null)" = "1" ]; then
      # Has explicit limit
      local pct=$(echo "scale=0; $mem_usage_mb * 100 / $mem_limit_mb" | bc 2>/dev/null)
      if [ "${pct:-0}" -gt "$MEM_WARN_THRESHOLD_PCT" ] 2>/dev/null; then
        warn_flag=1
        warn "⚠️ ${name}: ${pct}% of memory limit (${mem_display} / ${mem_limit_display})"
      fi
    else
      # No limit — warn if using too much absolute
      if [ "$(echo "$mem_usage_mb > $MEM_WARN_ABSOLUTE_MB" | bc -l 2>/dev/null)" = "1" ]; then
        warn_flag=1
        warn "⚠️ ${name}: ${mem_display} used (NO LIMIT!) — set mem_limit in docker-compose"
      fi
    fi
    
    if [ "$warn_flag" = "1" ]; then
      alerts=$((alerts + 1))
    fi
  done < <(docker stats --no-stream --format "{{.Name}}\t{{.Status}}\t{{.MemUsage}}\t{{.MemPctLimit}}\t{{.CPUPerc}}" 2>/dev/null | tail -n +2)

  echo ""
  echo "  Summary: ${GREEN}${running} running${NC}"
  [ "$stopped" -gt 0 ] && echo "           ${CYAN}${stopped} stopped${NC}"
  [ "$errors" -gt 0 ] && echo "           ${RED}${errors} errors${NC}"
  [ "$alerts" -gt 0 ] && echo "           ${YELLOW}${alerts} warnings${NC}"
  echo ""
  
  return $(( errors + ( alerts > 0 ? 1 : 0 ) ))
}

check_disk() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  DISK USAGE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  df -h / /var/lib/docker 2>/dev/null | tail -n +2 | while read -r line; do
    local pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
    local mount=$(echo "$line" | awk '{print $6}')
    if [ "${pct:-0}" -gt 90 ] 2>/dev/null; then
      error "💾 Disk ${mount} at ${pct}% — CRITICAL!"
    elif [ "${pct:-0}" -gt 80 ] 2>/dev/null; then
      warn "💾 Disk ${mount} at ${pct}% — getting full"
    else
      echo "  $(echo "$line" | awk '{print $6, $3, "used /", $2, "(" $5 ")"}')"
    fi
  done
}

check_cgroups() {
  # Verify cgroup v2 memory limits are respected
  if [ -f /sys/fs/cgroup/memory.current ]; then
    local cg_mem=$(cat /sys/fs/cgroup/memory.current)
    local cg_max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo "max")
    if [ "$cg_max" != "max" ]; then
      local cg_pct=$(( cg_mem * 100 / cg_max ))
      if [ "$cg_pct" -gt 90 ]; then
        warn "🔧 Cgroup memory at ${cg_pct}% (${cg_mem}/${cg_max})"
      fi
    fi
  fi
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║         🦆 LINA Health Check — $(date '+%Y-%m-%d %H:%M:%S')        ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  
  # Host info
  echo "  Host: $(uname -n)"
  echo "  Uptime: $(uptime -p | sed 's/up //')"
  echo "  Load: $(uptime | awk -F'load average:' '{print $2}' | xargs)"
  
  check_host_memory
  check_cpu_temp
  check_disk
  check_containers
  check_cgroups
  
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""
}

# ── CLI ─────────────────────────────────────────────────────────────────────

case "${1:-}" in
  --watch)
    log "Starting health check loop (every ${LOOP_INTERVAL}s)..."
    log "Press Ctrl+C to stop."
    while true; do
      main
      sleep "${LOOP_INTERVAL}"
    done
    ;;
  --install)
    log "Installing systemd timer..."
    
    # Service file
    sudo tee /etc/systemd/system/lina-health-check.service > /dev/null <<'SERVICEEOF'
[Unit]
Description=LINA Container Health Check
After=docker.service

[Service]
Type=oneshot
ExecStart=/home/fede/lina/bin/lina-health-check.sh
User=fede
Group=fede
SERVICEEOF

    # Timer file (cada 5 minutos)
    sudo tee /etc/systemd/system/lina-health-check.timer > /dev/null <<'TIMEREOF'
[Unit]
Description=Run LINA health check every 5 minutes
Requires=lina-health-check.service

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF

    sudo systemctl daemon-reload
    sudo systemctl enable lina-health-check.timer
    sudo systemctl start lina-health-check.timer
    log "✅ Systemd timer installed: lina-health-check.timer (every 5 min)"
    systemctl status lina-health-check.timer --no-pager
    ;;
  --help|-h)
    echo "Usage: lina-health-check.sh [OPTION]"
    echo ""
    echo "Options:"
    echo "  (none)     Run a single health check"
    echo "  --watch    Loop continuously every ${LOOP_INTERVAL}s"
    echo "  --install  Install as systemd timer (every 5 min)"
    echo "  --help     Show this help"
    ;;
  *)
    main
    ;;
esac

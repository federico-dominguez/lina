#!/usr/bin/env bash
set -euo pipefail

mem_pct=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%.0f",(t-a)*100/t}' /proc/meminfo)
load15=$(uptime | awk -F'load average:' '{print $2}' | awk -F',' '{gsub(/ /,"",$3); print $3}')
disk_pct=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
containers_down=$(docker ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null | { grep -v "Up" || true; } | wc -l)

alerts=""
[ "$mem_pct" -gt "90" ] 2>/dev/null && alerts="${alerts}🔥 RAM ${mem_pct}% "
[ "$(echo "${load15} > 8" | bc -l 2>/dev/null || echo 0)" = "1" ] && alerts="${alerts}⚡ Load ${load15} "
[ "$disk_pct" -gt "90" ] 2>/dev/null && alerts="${alerts}💾 Disk ${disk_pct}% "
[ "$containers_down" -gt "0" ] 2>/dev/null && alerts="${alerts}📦 ${containers_down} down "

if [ -n "$alerts" ]; then
    echo "⚠️ GUARDIA NOCTURNA $(date '+%H:%M'): ${alerts}"
    logger -t "overnight-guard" "ALERT: ${alerts} | RAM:${mem_pct}% Load:${load15} Disk:${disk_pct}% Down:${containers_down}"
fi
echo "$(date '+%H:%M') RAM:${mem_pct}% Load:${load15} Disk:${disk_pct}% Down:${containers_down}"

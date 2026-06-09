#!/bin/bash
# Restart goosed processes every 4h to prevent memory leaks
set -e

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "Restarting goosed agents..."

# Get PIDs of non-Docker goosed processes (PID 1804 and 27060)
for pid in $(pgrep -f "goosed agent" | tr '\n' ' '); do
    # Skip Docker-contained goosed (parent is containerd-shim)
    ppid=$(ps -o ppid= -p $pid 2>/dev/null | tr -d ' ')
    if echo "$ppid" | grep -qE "^1[0-9]{3}$|^[0-9]{1,3}$" 2>/dev/null || \
       ps -o cmd= -p $ppid 2>/dev/null | grep -q "systemd\|Goose"; then
        rss=$(ps -o rss= -p $pid 2>/dev/null | tr -d ' ')
        log "Sending SIGTERM to goosed PID=$pid (${rss}KB RSS)"
        kill -TERM $pid 2>/dev/null || log "  already gone"
    fi
done

log "Done."

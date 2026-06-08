#!/bin/bash
# bin/pipeline-scheduler.sh — Wrapper para ejecutar pipelines desde cron/systemd
set -e
cd "$(dirname "$0")/.."
source ~/.config/goose/secrets.env 2>/dev/null || true
export LINA_DB_URL="postgresql://lina:lina_dev@localhost:5432/lina"
exec python3 bin/pipeline-runner.py --parallel "$@"

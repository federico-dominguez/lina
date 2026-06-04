#!/bin/bash
# Pipeline: trabaja sobre issues reales del repo o features nuevas.
# Uso: ./start.sh --issue 42           # Trabaja en el issue #42
#      ./start.sh "descripción"        # Crea un issue nuevo y trabaja en él
#      ./start.sh --latest             # Toma el último issue abierto

set -e

cd /home/fede/lina

# Parse args
if [ "$1" = "--issue" ] && [ -n "$2" ]; then
    ISSUE="$2"
    echo "🚀 Pipeline sobre issue #$ISSUE"
    nohup python3 -u pipeline/pipeline_orchestrator.py --issue "$ISSUE" > /tmp/pipeline_run.log 2>&1 &
elif [ "$1" = "--latest" ]; then
    ISSUE=$(gh issue list --repo federico-dominguez/lina --state open --json number --jq '.[0].number' --limit 1 2>&1)
    echo "🚀 Pipeline sobre último issue abierto: #$ISSUE"
    nohup python3 -u pipeline/pipeline_orchestrator.py --issue "$ISSUE" > /tmp/pipeline_run.log 2>&1 &
else
    FEATURE="$*"
    if [ -z "$FEATURE" ]; then
        echo "Uso: $0 --issue <num> | --latest | 'descripción'"
        exit 1
    fi
    echo "🚀 Pipeline sobre nueva feature: $FEATURE"
    nohup python3 -u pipeline/pipeline_orchestrator.py "$FEATURE" > /tmp/pipeline_run.log 2>&1 &
fi

PID=$!
echo "PID: $PID"
echo "Log: tail -f /tmp/pipeline_run.log"
echo "Stop: ./pipeline/stop.sh"
echo "📱 Abrí el grupo Comm en tu celular"

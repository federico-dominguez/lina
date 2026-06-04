#!/bin/bash
# Batch runner: corre la pipeline sobre múltiples issues SECUENCIALMENTE.
# Un issue a la vez, en el mismo grupo Comm. Sin conflictos.
# Uso: ./pipeline/batch.sh --issues 113,91,89 --night

set -e

cd /home/fede/lina
LOG="/tmp/pipeline_batch.log"
RESULTS="/tmp/pipeline_batch_results.json"
ISSUES=""
MODE="batch"

# Parse args
while [ "$1" != "" ]; do
    case "$1" in
        --issues ) shift; ISSUES="$1";;
        --night  ) MODE="night";;
        --all    ) 
            ISSUES=$(gh issue list --repo federico-dominguez/lina --state open \
                     --json number --jq '.[].number' --limit 20 | paste -sd,)
            # Exclude pipeline-generated issues
            EXCLUDE="139,140,141,142"
            ISSUES=$(echo "$ISSUES" | tr ',' '\n' | grep -v -E "^(${EXCLUDE//,/|})$" | paste -sd,)
            # Also filter by label: exclude analysis/design/architecture issues
            FILTERED=""
            for N in $(echo "$ISSUES" | tr ',' ' '); do
                LABELS=$(gh issue view "$N" --repo federico-dominguez/lina --json labels --jq '[.[].name] | join(" ")' 2>/dev/null)
                SKIP=0
                for L in analysis design architecture pipeline-skip; do
                    case " $LABELS " in *" $L "*) SKIP=1;; esac
                done
                [ "$SKIP" = "0" ] && FILTERED="${FILTERED},${N}"
            done
            ISSUES="${FILTERED#,}"
            ;;
        * ) ISSUES="$1";;
    esac
    shift
done

if [ -z "$ISSUES" ]; then
    echo "❌ Uso: $0 --issues 113,91,89"
    echo "       $0 --night                   # defaults a issues prioritarios"
    echo "       $0 --all                     # todos los issues abiertos"
    exit 1
fi

# Default night mode issues (prioritarios)
if [ "$MODE" = "night" ]; then
    echo "🌙 Modo noche activado"
fi

echo "🚀 BATCH PIPELINE — $(date)"
echo "Issues: $ISSUES"
echo "Log: $LOG"
echo ""

# Clean stop flag
rm -f /tmp/pipeline_stop

TOTAL=$(echo "$ISSUES" | tr ',' '\n' | wc -l)
CURRENT=0
PASSED=0
FAILED=0
RESULTS_JSON="[]"
START_TIME=$(date +%s)

for ISSUE in $(echo "$ISSUES" | tr ',' '\n'); do
    CURRENT=$((CURRENT + 1))
    ISSUE=$(echo "$ISSUE" | xargs)  # trim

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  [$CURRENT/$TOTAL] Issue #$ISSUE"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    # Get issue title
    TITLE=$(gh issue view "$ISSUE" --repo federico-dominguez/lina --json title --jq '.title' 2>/dev/null || echo "Unknown")
    echo "  📋 $TITLE"
    echo ""

    # Check stop flag
    if [ -f /tmp/pipeline_stop ]; then
        echo "🛑 Stop detectado. Saliendo..."
        rm -f /tmp/pipeline_stop
        break
    fi

    # Run pipeline
    echo "  🏃 Corriendo pipeline..."
    TIMESTAMP=$(date +%s)
    
    python3 -u pipeline/pipeline_orchestrator.py --issue "$ISSUE" 2>&1 | tee -a "$LOG" | tail -5
    
    EXIT_CODE=${PIPESTATUS[0]}
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "  ✅ Issue #$ISSUE: PASÓ"
        PASSED=$((PASSED + 1))
    else
        echo "  ❌ Issue #$ISSUE: FALLÓ (exit=$EXIT_CODE)"
        FAILED=$((FAILED + 1))
    fi
    
    # Pequeña pausa entre issues
    sleep 5
    
    # Check stop flag again after each issue
    if [ -f /tmp/pipeline_stop ]; then
        echo "🛑 Stop detectado después de issue #$ISSUE"
        rm -f /tmp/pipeline_stop
        break
    fi
    
    echo ""
done

DURATION=$(( $(date +%s) - START_TIME ))
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  📊 BATCH COMPLETADO"
echo "  ⏱️  $((DURATION / 60)) minutos"
echo "  ✅ $PASSED pasaron | ❌ $FAILED fallaron | 📝 $TOTAL total"
echo "═══════════════════════════════════════════════════════════════"

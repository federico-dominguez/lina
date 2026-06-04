#!/bin/bash
# Detener la pipeline en ejecución.
# Crea flag de stop (la pipeline lo detecta entre pasos)
# y manda mensaje al grupo Comm via el bot bridge.

set -e

FLAG="/tmp/pipeline_stop"
echo "🛑 Pipeline detenida por usuario. Esperando nuevas instrucciones." > "$FLAG"
echo "🛑 Flag creado. La pipeline se detendrá al finalizar el paso actual."
echo ""

# Mandar notificación al grupo Comm
cd /home/fede/lina/pipeline
python3 -c "
import sys, asyncio
sys.path.insert(0, '.')
from pipeline_bot_bridge import send as tg_send
asyncio.run(tg_send('lina', '🛑 Pipeline detenida por el usuario. Esperá nuevas instrucciones. No hagas nada hasta que te asignen una tarea.'))
print('✅ Notificación enviada al grupo Comm')
" 2>&1 || echo "⚠️ No se pudo notificar"

# Matar proceso si está colgado
PID=$(ps aux | grep "pipeline_orchestrator" | grep -v grep | awk '{print $2}' | head -1)
if [ -n "$PID" ]; then
    echo ""
    echo "   PID del pipeline: $PID"
    echo "   Esperando que detecte el flag..."
    # Esperar hasta 30s a que termine solo
    for i in $(seq 1 30); do
        if ! kill -0 $PID 2>/dev/null; then
            echo "   ✅ Pipeline terminó sola (detectó el flag)"
            exit 0
        fi
        sleep 1
    done
    # Forzar
    echo "   ⚠️ Forzando parada..."
    kill $PID 2>/dev/null
    sleep 2
    kill -9 $PID 2>/dev/null
    echo "   ✅ Forzado"
else
    echo "ℹ️ No hay pipeline ejecutándose"
fi

rm -f "$FLAG"
echo "🛑 Pipeline detenida."

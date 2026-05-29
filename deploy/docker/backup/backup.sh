#!/bin/sh
# LINA — backup.sh: pg_dump horario con retención de 24 copias (≈ 1 día).
# Ejecutado por el servicio lina-backup cada 3600 segundos.
#
# Variables de entorno esperadas (del servicio docker compose):
#   PGHOST       lina-db
#   PGUSER       lina
#   PGDATABASE   lina
#   PGPASSWORD   (del POSTGRES_PASSWORD del stack)
#
# Archivo de salida: /backups/lina_YYYYMMDD_HHMMSS.sql.gz
# Retención:         últimas 24 copias (BACKUP_KEEP, sobreescribible por env)

set -e

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-24}"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%S")"
BACKUP_FILE="$BACKUP_DIR/lina_${TIMESTAMP}.sql.gz"

# ─── Crear directorio si no existe ───────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ─── Dump ────────────────────────────────────────────────────────────────────
echo "[lina-backup] Iniciando backup → $BACKUP_FILE"
pg_dump \
    --no-password \
    --format=plain \
    --encoding=UTF8 \
    | gzip -9 > "$BACKUP_FILE"

echo "[lina-backup] Backup completado: $(du -sh "$BACKUP_FILE" | cut -f1)"

# ─── Rotación: conservar sólo las últimas $BACKUP_KEEP copias ────────────────
EXCESS="$(ls -1t "$BACKUP_DIR"/lina_*.sql.gz 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)))"
if [ -n "$EXCESS" ]; then
    echo "$EXCESS" | xargs rm --
    echo "[lina-backup] Rotación completada. Copias retenidas: $BACKUP_KEEP"
fi

echo "[lina-backup] Total en disco: $(du -sh "$BACKUP_DIR" | cut -f1)"

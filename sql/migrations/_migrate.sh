#!/usr/bin/env bash
# LINA — Migration Runner
# Aplica migraciones SQL pendientes contra la base de datos vía Docker.
#
# Uso:
#   ./sql/migrations/_migrate.sh                  # aplica pendientes
#   ./sql/migrations/_migrate.sh --check          # solo muestra pendientes
#   ./sql/migrations/_migrate.sh --version 001    # fuerza una versión específica
#
# Requiere: docker (contenedor lina-db corriendo).
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_CONTAINER="${LINA_DB_CONTAINER:-docker-lina-db-1}"
DB_USER="${LINA_DB_USER:-lina}"
DB_NAME="${LINA_DB_NAME:-lina}"
MODE="apply"
FORCE_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check|-c) MODE="check" ;;
        --version|-v) FORCE_VERSION="$2"; shift ;;
        *) echo "❌ desconocido: $1"; exit 1 ;;
    esac
    shift
done

psql_exec() {
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -AtX "$@"
}

apply_one() {
    local file="$1"
    local version="$2"
    local name="$3"
    local checksum

    checksum=$(md5sum "$file" | cut -d' ' -f1)

    if [[ "$MODE" == "check" ]]; then
        echo "⏳ $version — $name ($(basename "$file"))"
        return 0
    fi

    echo "▶ Aplicando $version — $name..."
    # Copiar archivo al container y ejecutar
    docker cp "$file" "$DB_CONTAINER:/tmp/_migrate_${version}.sql" 2>/dev/null
    if docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -X -f "/tmp/_migrate_${version}.sql" 2>&1; then
        psql_exec -c "INSERT INTO public.schema_migrations (version, name, checksum) VALUES ('$version', '$name', '$checksum') ON CONFLICT (version) DO UPDATE SET checksum='$checksum', applied_at=NOW()" >/dev/null
        echo "✅ $version — $name aplicada"
        docker exec "$DB_CONTAINER" rm -f "/tmp/_migrate_${version}.sql" 2>/dev/null || true
    else
        echo "❌ $version — $name falló"
        return 1
    fi
}

# 1. Asegurar que la tabla de tracking existe
psql_exec -c "
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version     TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT        NOT NULL DEFAULT ''
)" >/dev/null 2>&1 || true

# 2. Listar migraciones disponibles (ordenadas por número)
echo "📋 Migraciones disponibles:"

# Recopilar datos
declare -a versions names files
applied=0
pending=0
failed=0

# Primero: recolectar archivos de migración
migration_files=()
while IFS= read -r f; do
    migration_files+=("$f")
done < <(find "$SELF_DIR" -maxdepth 1 -name '*.sql' ! -name '_*' | sort)

for f in "${migration_files[@]}"; do
    basename=$(basename "$f")
    version=$(echo "$basename" | sed -E 's/^([0-9]+).*/\1/')
    name=$(echo "$basename" | sed -E 's/^[0-9]+-(.*)\.sql$/\1/' | tr '-' ' ')

    # Forzar versión específica
    if [[ -n "$FORCE_VERSION" && "$version" == "$FORCE_VERSION" ]]; then
        echo "  🔄 $version — $name (forzada)"
        apply_one "$f" "$version" "$name" || ((failed++))
        continue
    fi

    # Verificar si ya fue aplicada
    state=$(psql_exec -c "SELECT 1 FROM public.schema_migrations WHERE version='$version'" 2>/dev/null || true)
    if [[ -n "$state" ]]; then
        echo "  ✅ $version — $name (aplicada)"
        ((applied++))
    else
        echo "  ⏳ $version — $name (pendiente)"
        if [[ "$MODE" == "apply" ]]; then
            apply_one "$f" "$version" "$name" || ((failed++))
            ((pending++))
        else
            ((pending++))
        fi
    fi
done

# 3. Resumen
echo "───"
if [[ "$MODE" == "check" ]]; then
    echo "📊 $applied aplicadas, $pending pendientes"
else
    echo "📊 $applied aplicadas previamente, $pending aplicadas ahora, $failed fallos"
fi

# Exit with error if any failed
[[ $failed -eq 0 ]] || exit 1

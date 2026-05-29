# Runbook 0004 — Recovery: reinicio del host y fallos de MCP

**Aplica a:** LINA stack Docker en `~/lina`  
**Última revisión:** 2026-05-29

---

## 1. Reinicio normal del host (Ubuntu con systemd)

Si `lina-goosed.service` está activo, los contenedores se levantan solos:

```bash
# Verificar que el servicio systemd arrancó
systemctl --user status lina-goosed.service

# Si está en failed:
systemctl --user reset-failed lina-goosed.service
systemctl --user start  lina-goosed.service

# Ver logs del servicio
journalctl --user -u lina-goosed.service -n 50
```

Si no usás systemd, levantar manualmente:

```bash
cd ~/lina
just docker-up        # equivale a: docker compose -f deploy/docker/docker-compose.yml up -d
```

Tiempo esperado hasta que todos los servicios estén healthy: **30–60 s**

---

## 2. Verificar estado del stack

```bash
cd ~/lina
docker compose -f deploy/docker/docker-compose.yml ps
```

Estado esperado: todos los servicios en `running (healthy)` o `running`.

Verificar conectividad con cada MCP (vía nginx gateway):

```bash
for port in 8101 8102 8103 8104 8105 8106; do
    python3 -c "import socket,sys; s=socket.socket(); r=s.connect_ex(('127.0.0.1',$port)); s.close(); print(f':$port → {\"OK\" if r==0 else f\"FAIL (errno={r})\"}')"
done
```

---

## 3. MCP individual caído (restart automático falla)

```bash
# Identificar el servicio caído
docker compose -f deploy/docker/docker-compose.yml ps

# Ver logs del MCP específico (ej: lina-mcp-secrets)
docker compose -f deploy/docker/docker-compose.yml logs --tail=50 lina-mcp-secrets

# Restart manual
docker compose -f deploy/docker/docker-compose.yml restart lina-mcp-secrets

# Si no arranca: rebuild + restart
docker compose -f deploy/docker/docker-compose.yml build lina-mcp-secrets
docker compose -f deploy/docker/docker-compose.yml up -d lina-mcp-secrets
```

---

## 4. PostgreSQL caído o corrupto

### 4a. Restart normal

```bash
docker compose -f deploy/docker/docker-compose.yml restart lina-db
# Esperar health: pg_isready
docker compose -f deploy/docker/docker-compose.yml ps lina-db
```

### 4b. Restaurar desde backup

```bash
# Listar backups disponibles
ls -lht ~/lina/  # o el path del volumen lina-backup-data
docker volume inspect lina_lina-backup-data

# Copiar el backup más reciente al host
BACKUP_VOL=$(docker volume inspect lina_lina-backup-data -f '{{.Mountpoint}}')
ls -lt "$BACKUP_VOL"/*.sql.gz | head -5

# Levantar sólo el DB
docker compose -f deploy/docker/docker-compose.yml up -d lina-db
docker compose -f deploy/docker/docker-compose.yml ps lina-db  # esperar healthy

# Restaurar (ajustar nombre del archivo)
BACKUP_FILE="$BACKUP_VOL/lina_20260529_030000.sql.gz"
gunzip -c "$BACKUP_FILE" | docker exec -i lina-lina-db-1 psql -U lina -d lina

# Verificar
docker exec lina-lina-db-1 psql -U lina -d lina -c "SELECT count(*) FROM memories;"
```

### 4c. Volumen corrupto: reconstruir desde cero

```bash
# DESTRUCTIVO — sólo si el backup restore falló
docker compose -f deploy/docker/docker-compose.yml down
docker volume rm lina_lina-db-data
docker compose -f deploy/docker/docker-compose.yml up -d lina-db
# Luego restaurar desde backup (ver 4b)
```

---

## 5. nginx gateway caído (MCPs inaccesibles desde host)

Los MCPs siguen corriendo; sólo el acceso externo (goosed en el host) está
afectado.

```bash
docker compose -f deploy/docker/docker-compose.yml restart lina-mcp-gateway

# Ver config nginx (verificar que el archivo se monta correctamente)
docker compose -f deploy/docker/docker-compose.yml exec lina-mcp-gateway nginx -t
```

**Acceso directo a MCPs sin nginx (emergencia):**
Los MCPs no exponen puertos al host directamente. Para acceso de emergencia:

```bash
# Exponer temporalmente un MCP (no recomendado en producción)
docker run --rm --network lina_default -p 127.0.0.1:9999:8000 \
    lina-mcp-secrets:latest  # o el MCP que necesites
```

---

## 6. Backup manual

```bash
# Ejecutar backup inmediato (sin esperar la hora)
docker compose -f deploy/docker/docker-compose.yml exec lina-backup \
    sh /usr/local/bin/lina-backup

# Ver backups existentes
BACKUP_VOL=$(docker volume inspect lina_lina-backup-data -f '{{.Mountpoint}}')
ls -lh "$BACKUP_VOL"/*.sql.gz
```

---

## 7. Reseteo completo del stack (último recurso)

```bash
# Detener todo
docker compose -f deploy/docker/docker-compose.yml down

# OPCIONAL: eliminar datos (DESTRUCTIVO)
# docker volume rm lina_lina-db-data lina_lina-secrets-data lina_lina-backup-data

# Levantar limpio
docker compose -f deploy/docker/docker-compose.yml up -d --build
```

---

## 8. Diagrama de servicios y dependencias

```
lina-db (postgres:16-alpine)
  ├── lina-mcp-db        (MCPs → lina-db healthcheck)
  └── lina-backup        (pg_dump cada 3600s → lina-backup-data)

lina-mcp-gateway (nginx:1.27-alpine) ← puertos 8101-8106 en localhost
  ├── lina-mcp-secrets   :8000 (interno)
  ├── lina-mcp-fs-safe   :8000 (interno)
  ├── lina-mcp-shell-policy :8000 (interno)
  ├── lina-mcp-systemd-user :8000 (interno)
  ├── lina-mcp-moodle    :8000 (interno, red lina-moodle-net)
  └── lina-mcp-db        :8000 (interno)

goosed (host) → lina-mcp-gateway:8101-8106
```

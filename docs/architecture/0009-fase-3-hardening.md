# ADR 0009 — Fase 3: Hardening a nivel producción

**Estado:** Aceptado  
**Fecha:** 2026-05-29  
**Autores:** Federico Domínguez, LINA

---

## Contexto

Con Fase 2 (PR #23) los MCPs de LINA corren en contenedores Docker con
transporte SSE. Los contenedores levantan, pero su postura de seguridad es
básica: filesystem mutable, privilegios no restringidos, sin rate limiting,
sin backups automáticos ni logging estructurado.

Fase 3 eleva la baseline a nivel producción: un reinicio del host o un fallo
de MCP no pierde datos ni requiere intervención manual.

---

## Decisiones

### 3.1 + 3.2 — `read_only: true` y `x-mcp-defaults`

**Decisión:** Todos los MCPs usan `read_only: true` en el filesystem del
contenedor, combinado con `cap_drop: ALL`, `security_opt: no-new-privileges`
y `tmpfs: /tmp`.

**Racional:**
- `read_only: true` — impide que código comprometido en un MCP persista
  archivos en el FS del contenedor. Las escrituras legítimas van a volúmenes
  nombrados o a `/tmp` (tmpfs).
- `cap_drop: ALL` — elimina todas las capabilities Linux. Ningún MCP necesita
  capacidades especiales; systemd-user usa D-Bus vía socket unix que no
  requiere caps.
- `no-new-privileges` — impide que un proceso hijo gane privilegios via
  setuid/setgid bits.
- `PYTHONDONTWRITEBYTECODE=1` — evita que Python intente escribir `.pyc` al
  venv (que está en el FS read-only).
- `x-mcp-defaults` YAML anchor centraliza estas reglas; agregar un MCP nuevo
  hereda automáticamente toda la postura de seguridad.

**Alternativas rechazadas:**
- Distroless base image (ADR 0006): no descartada, pero las imágenes
  `python:3.13-slim-bookworm` con `read_only` logran una superficie de ataque
  comparable con mayor compatibilidad con uv/FastMCP.

---

### 3.3 — Rate limiting vía nginx gateway

**Decisión:** Un servicio `lina-mcp-gateway` (nginx:1.27-alpine) actúa como
proxy reverso para los 6 MCPs. Los MCPs NO exponen puertos directamente; sólo
nginx lo hace en 127.0.0.1:8101–8106.

Límites configurados en `deploy/docker/nginx/nginx.conf`:
| MCP          | Zona         | Límite     | Burst |
|--------------|--------------|------------|-------|
| shell-policy | mcp_shell    | 30 req/min | 5     |
| moodle       | mcp_moodle   | 20 req/min | 5     |
| resto (×4)   | mcp_default  | 120 req/min| 20    |

**Racional:**
- Los MCPs más "costosos" o riesgosos (shell, moodle externo) tienen límites
  más bajos para atrapar loops del agente antes de que causen daño.
- nginx es la única entrada al stack MCP desde el host; simplifica el modelo
  de seguridad (un punto de control).
- `limit_req_status 429` devuelve HTTP 429 Too Many Requests, legible por
  goosed/LLM.

**Alternativas rechazadas:**
- Rate limiting en cada FastMCP server (slowapi): requería añadir dependencia
  externa y código repetido en 6 MCPs.
- Traefik: overhead mayor para este caso de uso.

---

### 3.4 + 3.5 — Esquema `audit` en PostgreSQL

**Decisión:** Se crea un schema PostgreSQL `audit` con:
- `audit.tool_calls` — tabla principal (reemplaza `public.audit_logs`)
- `audit.daily_summary` — vista de resumen diario por MCP/tool
- `public.audit_logs` — vista de compatibilidad hacia atrás

La tabla agrega columna `mcp` (origen del call) y `duration_ms` (latencia).

**Racional:**
- Separar en schema propio facilita permisos granulares en el futuro (un
  usuario `audit_reader` read-only, por ejemplo).
- `daily_summary` responde directamente a la pregunta "¿cuánto usé LINA hoy
  y qué tools fueron más costosas?", útil para el cost tracking manual.
- Migración idempotente en `sql/migrations/001-audit-schema.sql`.

---

### 3.6 — Backups automáticos

**Decisión:** Servicio `lina-backup` (imagen `postgres:16-alpine`) ejecuta
`pg_dump` cada hora. Retención: 24 copias (~1 día) en volumen `lina-backup-data`.

**Racional:**
- `pg_dump --format=plain | gzip -9` produce archivos legibles y comprimidos
  (~90% compresión típica en datos de texto/JSONB).
- Retención de 24h cubre el caso de "me di cuenta del error esta mañana".
- El mismo binario `postgres:16-alpine` que se usa para `lina-db` evita
  versión mismatch en pg_dump.

**Limitaciones conocidas:**
- Backup en el mismo host que el DB. Para resiliencia real, montar
  `lina-backup-data` sobre almacenamiento externo (NFS, rclone a S3, etc.).
- Sin cifrado de backups. Los datos son personales de Federico, bajo su
  control físico.

---

### 3.7 — Health checks y auto-restart

**Decisión:** 
- Todos los MCPs tienen `HEALTHCHECK` definido en `Dockerfile.mcp` (TCP connect).
- `lina-mcp-db` usa `depends_on: lina-db: condition: service_healthy`.
- `lina-mcp-gateway` tiene `depends_on` sobre todos los MCPs (`service_started`).
- `restart: unless-stopped` en todos los servicios.

**Racional:** `service_healthy` garantiza que el DB acepta conexiones antes
de que el MCP intente conectarse, evitando errores de arranque en race
conditions de startup.

---

### 3.8 — Logging estructurado JSON

**Decisión:** Todos los servicios usan `logging: driver: json-file` con
`max-size: 10m` y `max-file: 3` (rotación automática, ~30MB por servicio).

**Racional:** `docker logs` devuelve JSON nativo con timestamp, container ID y
nivel de log. Sin configuración adicional en las aplicaciones Python
(stdout/stderr es capturado por Docker).

**Nota:** `lina-backup` usa `max-size: 5m / max-file: 2` (logs menos
verbosos); goosed usa `max-size: 20m / max-file: 5` (más verbose, tiene el
historial de conversación).

---

## Consecuencias

**Positivas:**
- Surface de ataque reducida: contenedores sin caps, FS inmutable.
- Auto-recuperación ante reinicios del host o crashes de MCP.
- Auditoría completa con vista de resumen diario.
- Rate limiting previene loops del agente en MCPs costosos.
- Backups automáticos con retención de 1 día.

**Negativas:**
- `lina-mcp-gateway` es un punto de fallo único (mitigado: nginx es muy
  estable; MCPs siguen accesibles desde dentro del stack sin nginx).
- Smoke test (`test_mcp_containers.sh`) ahora requiere levantar nginx además
  de los MCPs para verificar conectividad en puertos 8101–8106.
- `lina-backup` agrega un servicio más al stack (impacto mínimo, sólo
  `postgres:16-alpine` que ya está en el docker layer cache).

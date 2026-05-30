# LINA MCP Guide

Guía técnica completa del ecosistema de servidores MCP de LINA: arquitectura, 
configuración Docker, gestión en producción y cómo agregar nuevos MCPs.

---

## 1. Arquitectura general

```
goosed ──────────────────────────────────────────────────────►
         (streamable_http)         lina-mcp-gateway (nginx)
                                   ┌───────────────────────┐
         :8101 ◄────────────────── │  lina-mcp-secrets     │
         :8102 ◄────────────────── │  lina-mcp-fs-safe     │
         :8103 ◄────────────────── │  lina-mcp-shell-policy│
         :8104 ◄────────────────── │  lina-mcp-systemd-user│
         :8105 ◄────────────────── │  lina-mcp-moodle      │
         :8106 ◄────────────────── │  lina-mcp-db          │
         :8107 ◄────────────────── │  lina-mcp-github      │
         :8108 ◄────────────────── │  lina-mcp-gitlab      │
         :8109 ◄────────────────── │  lina-mcp-gcalendar   │
         :8110 ◄────────────────── │  lina-mcp-gns3        │
                                   └───────────────────────┘
```

- Cada MCP es un contenedor Python que expone `POST /mcp` via MCP Streamable HTTP.
- El contenedor `lina-mcp-gateway` (nginx) hace proxy reverso a cada MCP en su puerto.
- `goosed` se conecta a `http://lina-mcp-gateway:<puerto>/mcp` vía `streamable_http`.
- Los MCPs **no tienen ports mapeados al host** — solo son accesibles via el gateway.

### Transporte

| Protocolo       | Config goosed        | Estado  |
|-----------------|----------------------|---------|
| streamable_http | `type: streamable_http` | ✅ activo |
| sse             | `type: sse`          | ❌ deprecado |
| stdio           | `type: stdio`        | solo builtin |

### Rate limiting (nginx)

| Zona         | Rate     | Burst | Aplica a                     |
|--------------|----------|-------|------------------------------|
| mcp_default  | 120 r/m  | 50    | Todos excepto shell y moodle |
| mcp_shell    | 30 r/m   | 5     | lina-shell-policy            |
| mcp_moodle   | 20 r/m   | 5     | lina-moodle                  |

> **Nota**: `burst=50` es necesario porque goosed inicializa ~10 extensiones en 
> paralelo al cargar una sesión (~2 requests por extensión = 20 burst mínimo).
> Un burst menor causa HTTP 429 que impide que las extensions aparezcan en el 
> tool list del LLM.

---

## 2. Clean Architecture (patrón de código)

Todos los MCPs siguen la misma estructura de capas:

```
mcps/<nombre>/
├── pyproject.toml          # nombre = lina-<nombre>
└── src/
    └── lina_<nombre>/
        ├── domain/         # entidades, interfaces, reglas de negocio
        │   └── entities.py
        ├── application/    # casos de uso, orquestación
        │   └── use_cases.py
        ├── infrastructure/ # implementaciones concretas (HTTP, DB, OAuth)
        │   └── client.py
        └── server.py       # punto de entrada FastMCP, definición de tools
```

### server.py mínimo

```python
from fastmcp import FastMCP
from lina_mimc.application.use_cases import MyUseCase

mcp = FastMCP("lina-mimc")

@mcp.tool()
async def my_tool(param: str) -> str:
    """Descripción de la tool."""
    return await MyUseCase().execute(param)

if __name__ == "__main__":
    import os
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    port = int(os.getenv("MCP_PORT", "8000"))
    mcp.run(transport=transport, host="0.0.0.0", port=port)
```

### pyproject.toml mínimo

```toml
[project]
name = "lina-mimc"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastmcp>=2.0"]

[project.scripts]
lina-mimc = "lina_mimc.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## 3. Agregar un nuevo MCP

### Paso 1: Código fuente

```bash
mkdir -p mcps/mimc/src/lina_mimc/{domain,application,infrastructure}
touch mcps/mimc/src/lina_mimc/{__init__,server}.py
# Implementar según Clean Architecture
```

### Paso 2: docker-compose.yml

Agregar el servicio en `deploy/docker/docker-compose.yml`:

```yaml
  lina-mcp-mimc:
    <<: *mcp-defaults
    build:
      context: ../..
      dockerfile: deploy/docker/Dockerfile.mcp
      args:
        MCP_DIR: mcps/mimc
        MCP_CMD: lina-mimc
    image: lina-mcp-mimc:latest
    environment:
      MCP_TRANSPORT: streamable-http
      MCP_PORT: "8000"
      # Variables de entorno específicas del MCP
      MIMC_API_KEY: ${MIMC_API_KEY}
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8000/mcp/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

> **Puerto**: asignar el siguiente disponible (actualmente `:8111`).

### Paso 3: nginx.conf

Agregar en `deploy/docker/nginx/nginx.conf`:

```nginx
# ─── :8111 — lina-mimc ───────────────────────────────────────────────
server {
    listen 8111;
    location / {
        limit_req zone=mcp_default burst=50 nodelay;
        limit_req_status 429;
        proxy_pass http://lina-mcp-mimc:8000;
    }
}
```

### Paso 4: depends_on en lina-mcp-gateway

```yaml
  lina-mcp-gateway:
    depends_on:
      - lina-mcp-mimc   # agregar aquí
```

### Paso 5: goose.config.yaml.tmpl

Agregar la extensión en `config/goose.config.yaml.tmpl`:

```yaml
  lina-mimc:
    name: lina-mimc
    display_name: LINA MiMC
    type: streamable_http
    uri: http://lina-mcp-gateway:8111/mcp
    enabled: true
    timeout: 60
```

### Paso 6: ports en lina-mcp-gateway

```yaml
  lina-mcp-gateway:
    ports:
      - "127.0.0.1:8111:8111"   # agregar aquí
```

### Paso 7: Rebuild y deploy

```bash
cd deploy/docker
docker compose build lina-mcp-mimc
docker compose up -d lina-mcp-mimc
docker compose exec lina-mcp-gateway nginx -s reload
# Reiniciar goosed para que cargue la nueva extensión
docker compose restart goosed
```

---

## 4. Gestión en producción

### Ver estado de todos los MCPs

```bash
cd deploy/docker
docker compose ps
```

Contenedores en producción:

| Contenedor                 | Puerto gateway | Descripción              |
|----------------------------|----------------|--------------------------|
| docker-lina-mcp-secrets-1  | :8101          | Gestor de secretos       |
| docker-lina-mcp-fs-safe-1  | :8102          | Filesystem con allowlist |
| docker-lina-mcp-shell-policy-1 | :8103      | Shell con policy         |
| docker-lina-mcp-systemd-user-1 | :8104      | systemd user units       |
| docker-lina-mcp-moodle-1   | :8105          | API Moodle UTEC          |
| docker-lina-mcp-db-1       | :8106          | Base de datos LINA       |
| docker-lina-mcp-github-1   | :8107          | GitHub API               |
| docker-lina-mcp-gitlab-1   | :8108          | GitLab API               |
| docker-lina-mcp-gcalendar-1 | :8109         | Google Calendar          |
| docker-lina-mcp-gns3-1     | :8110          | GNS3 API                 |
| docker-lina-mcp-gateway-1  | :8101-8110     | nginx proxy + rate limit |

### Reiniciar un MCP individual (sin downtime para otros)

```bash
cd deploy/docker
docker compose restart lina-mcp-<nombre>
```

Ejemplo — reiniciar solo gcalendar:
```bash
docker compose restart lina-mcp-gcalendar
```

### Recargar nginx config (sin downtime)

```bash
# Verificar config primero
docker compose exec lina-mcp-gateway nginx -t

# Recargar si está bien
docker compose exec lina-mcp-gateway nginx -s reload
```

> **Importante**: si se editó `nginx.conf` en el host, verificar que el cambio
> esté en el container antes de hacer reload:
> ```bash
> docker exec docker-lina-mcp-gateway-1 grep "burst=" /etc/nginx/nginx.conf
> ```
> Si el valor no cambió, hay que reiniciar el container completo:
> ```bash
> docker compose restart lina-mcp-gateway
> ```

### Forzar recarga de extensiones en goosed

Cuando se agrega o modifica un MCP, goosed necesita cargar sus tools nuevas.
Las opciones (de menor a mayor impacto):

```bash
# Opción A: el gateway de Telegram llama agent/resume automáticamente
# en el próximo mensaje — carga tools actualizadas sin reiniciar nada.

# Opción B: reiniciar solo goosed (sesión se mantiene en el gateway)
cd deploy/docker
docker compose restart goosed

# Opción C: reiniciar gateway + goosed (sesión se resetea)
docker compose restart lina-gateway goosed
```

### Ver logs en tiempo real

```bash
# Un MCP específico
docker logs -f docker-lina-mcp-gcalendar-1

# Todos los MCPs + gateway
cd deploy/docker && docker compose logs -f lina-mcp-gateway lina-mcp-gcalendar

# goosed
docker logs -f docker-goosed-1 2>&1 | grep -E "Failed|extension|gcal"
```

### Diagnosticar 429 Too Many Requests

Si goosed logs muestran `HTTP 429 Too Many Requests` al cargar extensiones:

1. Verificar `burst` actual en nginx:
   ```bash
   docker exec docker-lina-mcp-gateway-1 grep "burst=" /etc/nginx/nginx.conf
   ```
2. Si es < 50, aumentarlo en `deploy/docker/nginx/nginx.conf` y recargar.
3. Reiniciar goosed para que cargue las extensiones sin 429.

---

## 5. Consideraciones de seguridad

### UID de los contenedores

- Los MCPs default corren como `mcp` (uid=1001) — definido en `Dockerfile.mcp`.
- `lina-mcp-fs-safe` y `lina-mcp-gcalendar` usan `user: "${UID:-1000}"` para
  acceder a archivos del host con el mismo uid que el usuario fede.
- Los volúmenes Docker (`lina-gcalendar-token`, `lina-logs`) se inicializan con
  el uid del proceso que los crea. Si hay problemas de `Permission denied`:
  ```bash
  docker exec -u root <container> ls -la /ruta/problema
  ```

### Archivos de credenciales

- Secretos: siempre via MCP `lina-secrets` (Vault/keyring). Nunca en archivos `.env` del repo.
- Tokens OAuth (gcalendar): en el volumen Docker `lina-gcalendar-token` → `/run/gcalendar/token.json`
- `credentials.json` de gcalendar se monta como `:ro` desde `${GCALENDAR_CREDENTIALS_FILE}`.

### Paths en lina-fs-safe

El MCP `lina-fs-safe` corre en Docker con `HOME=/home/user`. El host se monta en `/home/user`. Usar **siempre rutas absolutas**:

| Desde LINA                | Ruta correcta en lina-fs-safe |
|---------------------------|-------------------------------|
| `~/lina/README.md`        | `/home/user/lina/README.md`   |
| `~/Documents/foo.txt`     | `/home/user/Documents/foo.txt`|
| `~/IdeaProjects/my-app/`  | `/home/user/IdeaProjects/my-app/` |

Las llamadas con `~/` pueden fallar si goosed expande la tilde a `/root` antes de enviarla al MCP.

---

## 6. Agregar MCPs con OAuth (patrón gcalendar)

Para MCPs que requieren OAuth (Google, etc.):

1. Crear volumen named para el token:
   ```yaml
   volumes:
     lina-<servicio>-token:
   ```

2. Montar credentials como `:ro` y token como `:rw`:
   ```yaml
   volumes:
     - ${CREDENTIALS_FILE}:/run/<servicio>/credentials.json:ro
     - lina-<servicio>-token:/run/<servicio>
   ```

3. Definir `read_only: false` (override de `*mcp-defaults`).

4. Agregar `user: "${UID:-1000}"` para que el proceso tenga permiso de escritura
   sobre el volumen (que fue creado con uid del host).

5. Pasar las rutas via variables de entorno:
   ```yaml
   environment:
     SERVICE_CREDENTIALS_FILE: /run/<servicio>/credentials.json
     SERVICE_TOKEN_FILE: /run/<servicio>/token.json
   ```

---

## 7. Troubleshooting rápido

| Síntoma | Causa probable | Fix |
|---------|---------------|-----|
| LINA dice que no tiene extensión X | 429 en session init | Verificar burst=50 en nginx, reiniciar goosed |
| `Permission denied` en token OAuth | uid mismatch | Agregar `user: "${UID:-1000}"` al container |
| Extension cargada pero tools no funcionan | OAuth expirado | Inspeccionar logs del container, re-autenticar |
| `~/lina/...` no encontrado | Tilde no expandida en fs-safe | Usar `/home/user/lina/...` |
| goosed logs: `SSE is unsupported` | Extension configurada como `type: sse` | Cambiar a `type: streamable_http` en config |
| 406 Not Acceptable al probar endpoint | Request sin `Accept: text/event-stream` | Normal — usar cliente MCP, no curl plano |

# Runbook 0005 — Reload MCP container de forma segura

> Cómo recargar un MCP en caliente sin reiniciar `goosed` ni perder la sesión activa.

## Cuándo usar `reload_mcp`

Después de cualquiera de estos cambios en un MCP:

| Cambio | Reload necesario | Notas |
|---|---|---|
| Bugfix en una tool existente | ✅ Sí | El cambio entra al hacer pull + rebuild + reload |
| Nueva tool agregada (decorador `@mcp.tool()`) | ✅ Sí | `goosed` re-descubre las tools al próximo handshake |
| Cambio de prompts internos / strings | ✅ Sí | Reload barato |
| Cambio en `pyproject.toml` (dependencia nueva) | ✅ Sí, pero **antes hacer `docker compose build <service>`** | Rebuild es obligatorio |
| Cambio en `mcp-registry.yaml` (rutas, puertos) | ⚠️ Reload no alcanza | Requiere reiniciar `goosed` para releer config |
| Cambio en `config/policies.yaml` | ⚠️ Reload no aplica | El consumidor (orchestrator, gateway) recarga vía SIGHUP/endpoint dedicado |

## Por qué funciona (insight arquitectural)

Los MCPs usan transporte **`streamable-http`** (cada tool call = request HTTP independiente). `goosed` no mantiene una conexión persistente al MCP — cada llamada abre un socket nuevo. Si el container se reinicia:

1. Tool call en flight: puede fallar con `connection refused` durante ~1-3s.
2. Tool call siguiente: llega al container nuevo y funciona normalmente.

**Conclusión:** restart del container es transparente para `goosed`. **No se necesitan cambios en Rust.**

## Sintaxis básica

Desde LINA (a través del MCP `lina-shell-policy`):

```text
sh_run("reload_mcp('lina-fs-safe')")
```

Con timeout custom (default 30s, cap 120s):

```text
sh_run("reload_mcp('lina-db', health_timeout=60)")
```

Respuesta exitosa:

```json
{
  "success": true,
  "name": "lina-fs-safe",
  "service": "lina-mcp-fs-safe",
  "health_url": "http://localhost:8102/",
  "duration_seconds": 3.21
}
```

Respuesta fallida (MCP desconocido):

```json
{
  "success": false,
  "name": "lina-typo",
  "error": "MCP desconocido: 'lina-typo'. Conocidos: lina-db, lina-fs-safe, lina-gcalendar, lina-github, lina-gitlab, lina-moodle, lina-secrets, lina-shell-policy, lina-systemd-user"
}
```

## MCPs reloadables

Definidos en `_RELOADABLE_MCPS` en
[mcps/shell-policy/src/lina_shell_policy/server.py](../../mcps/shell-policy/src/lina_shell_policy/server.py#L232):

| Nombre | Servicio Docker | Health URL |
|---|---|---|
| `lina-secrets` | `lina-mcp-secrets` | `http://localhost:8101/` |
| `lina-fs-safe` | `lina-mcp-fs-safe` | `http://localhost:8102/` |
| `lina-shell-policy` | `lina-mcp-shell-policy` | `http://localhost:8103/` |
| `lina-systemd-user` | `lina-mcp-systemd-user` | `http://localhost:8104/` |
| `lina-moodle` | `lina-mcp-moodle` | `http://localhost:8105/` |
| `lina-db` | `lina-mcp-db` | `http://localhost:8106/` |
| `lina-gitlab` | `lina-mcp-gitlab` | `http://localhost:8107/` |
| `lina-github` | `lina-mcp-github` | `http://localhost:8108/` |
| `lina-gcalendar` | `lina-mcp-gcalendar` | `http://localhost:8109/` |

> ⚠️ Reload de `lina-shell-policy` se reinicia a sí mismo. La tool call que disparó el reload puede fallar; el efecto persiste y el siguiente call funciona.

## Health check — semántica exacta

Función [`_wait_for_health`](../../mcps/shell-policy/src/lina_shell_policy/server.py#L249):

- Sondea `health_url` con `urllib.request.urlopen(url, timeout=2)`.
- **HTTPError** (4xx/5xx) → `True` (el servidor está arriba, sólo no le gusta `GET /`).
- **URLError / OSError** → retry cada 1s hasta `health_timeout`.
- Cap superior: `min(health_timeout, 120)`.

Si el health timeout vence, `success=False` con `error="health check timeout tras Xs"`. El container puede estar arriba pero lento — verificar con `docker ps`.

## Prerrequisitos

1. `LINA_SHELL_ALLOW_SUDO=1` en el entorno de `lina-mcp-shell-policy` (ya configurado en `deploy/docker/docker-compose.yml`).
2. Binario `lina-deploy` en `/usr/local/bin/lina-deploy` (override con `LINA_DEPLOY_BIN=`).
3. Service name presente en el allowlist `ALLOWED_SERVICES` de `lina-deploy`.

## Rate limiting

`lina-shell-policy` no limita `reload_mcp` por sí mismo, pero el rate-limit global de tool calls (~30/min por MCP en `goosed`) aplica. Múltiples reloads consecutivos del mismo MCP no tienen sentido — el segundo reinicia un container que recién terminó de levantar.

## Rollback manual si el container nuevo crashea

Si después de un `reload_mcp` el container queda en loop o falla health forever:

```bash
# 1. Diagnóstico
cd /home/fede/lina/deploy/docker
docker ps -a --filter name=lina-mcp-<name>
docker compose logs --tail=50 lina-mcp-<name>

# 2. Si la imagen anterior aún tiene tag (latest = recién buildeada, suele
#    haber una previa por digest):
docker images | grep lina-mcp-<name>
# Re-tag manualmente la versión previa por digest y forzar redeploy:
docker tag lina-mcp-<name>:<sha-previa> lina-mcp-<name>:latest
docker compose up -d lina-mcp-<name>

# 3. Si no hay imagen previa, revertir el código y rebuild:
git revert <commit-malo>
docker compose build lina-mcp-<name>
docker compose up -d lina-mcp-<name>
```

> 💡 **Recomendación operativa**: antes de un `reload_mcp` en producción, verificar que el rebuild fue exitoso (`docker compose build <service>` retorna exit 0 y la imagen tiene `latest` apuntando al digest nuevo).

## Tests asociados

- Unit (60+ casos): [mcps/shell-policy/tests/test_reload_mcp.py](../../mcps/shell-policy/tests/test_reload_mcp.py)
- Integration real: [tests/integration/test_reload_mcp_real.py](../../tests/integration/test_reload_mcp_real.py) — requiere Docker activo y `LINA_E2E_REAL_RELOAD=1`.
- E2E Telegram: [tests/e2e/telegram/scenarios/test_issues_79_82_48.py](../../tests/e2e/telegram/scenarios/test_issues_79_82_48.py)

## Historia

- 2026-05-29 — Implementado en PR #97 (issue #48). Insight clave: streamable-http hace el reload transparente, no se tocó Rust.
- 2026-06-01 — Runbook publicado (PR #99, issue #98).

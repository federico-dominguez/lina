# Convenciones para Goose dentro de ~/lina

- Escribir SOLO dentro de las rutas del MCP `fs-safe` (allowlist).
- Comandos shell pasan por `shell-policy`; los marcados `sudo` requieren confirmación humana.
- Secretos vía MCP `secrets`. NUNCA en `config.yaml` ni en archivos del repo.
- Cada MCP propio sigue Clean Architecture: `domain/ application/ infrastructure/ server.py`.
- Cada tool emite un evento `ToolInvoked` (cuando exista el bus); por ahora se loguea a stderr.
- Recipes se versionan en `recipes/`.

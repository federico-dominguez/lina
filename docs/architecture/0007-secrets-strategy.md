# ADR 0007 — Secrets Strategy

**Status:** Accepted  
**Date:** 2025-01-27  
**Deciders:** Federico Dominguez  
**Tags:** secrets, security, keyring, docker, vault

---

## 1. Contexto

LINA necesita gestionar secretos en múltiples contextos:

- **Desarrollo local (Fase 0/1)**: Goose corre como servicio systemd en la laptop de Federico. Los secretos son tokens de API (Telegram, DeepSeek, Moodle, GitLab).
- **Containerizado (Fase 2+)**: Los MCPs corren en contenedores Docker. El keyring de GNOME no está disponible dentro de un contenedor.
- **Potencial despliegue compartido (Fase 4+)**: Si LINA escala a múltiples usuarios o ambientes, se necesita una solución de secrets management centralizada.

Los secretos actuales manejados por LINA:

| Secret                | MCP usuario        | Env var             |
|-----------------------|--------------------|---------------------|
| `DEEPSEEK_API_KEY`    | goosed (gateway)   | `DEEPSEEK_API_KEY`  |
| `TELEGRAM_BOT_TOKEN`  | goosed (gateway)   | `TELEGRAM_BOT_TOKEN`|
| `MOODLE_USERNAME`     | lina-moodle        | `MOODLE_USERNAME`   |
| `MOODLE_PASSWORD`     | lina-moodle        | `MOODLE_PASSWORD`   |
| `GITLAB_TOKEN`        | lina-gitlab        | `GITLAB_TOKEN`      |
| `GITHUB_TOKEN`        | lina-github        | `GITHUB_TOKEN`      |

---

## 2. Decisión

**Estrategia en capas según la fase de despliegue:**

| Fase     | Mecanismo                        | Justificación                               |
|----------|----------------------------------|---------------------------------------------|
| 0/1      | `libsecret` vía `keyring` Python | Ya implementado, cero fricción en laptop    |
| 2 (Docker)| Docker secrets + env vars       | Estándar para compose, sin keyring requerido|
| 3+       | HashiCorp Vault / sops+age       | Para multi-instancia o CI/CD avanzado       |

---

## 3. Fase 0/1: libsecret (actual)

El MCP `lina-secrets` usa la librería `keyring` de Python, que en Linux con GNOME usa `SecretService` / `libsecret`. Los secretos se almacenan en el keyring del usuario, protegidos por la sesión de login.

**Ventajas:**
- Cero configuración extra para Federico en laptop Ubuntu.
- Los secretos nunca tocan el filesystem en texto plano.
- `lina-secrets` provee una API MCP para leer/escribir secretos desde LINA.

**Limitaciones:**
- No disponible en contenedores headless (sin D-Bus de usuario).
- No portable entre máquinas.

---

## 4. Fase 2: Docker secrets

Docker Compose y Docker Swarm soportan [Docker secrets](https://docs.docker.com/engine/swarm/secrets/): archivos montados en `/run/secrets/<name>`, legibles solo por el proceso del contenedor.

### 4.1 Flujo para MCPs

```yaml
# compose.yml (Fase 2)
services:
  lina-moodle:
    image: lina-moodle:latest
    secrets:
      - moodle_password
      - moodle_username
    environment:
      MOODLE_USERNAME_FILE: /run/secrets/moodle_username
      MOODLE_PASSWORD_FILE: /run/secrets/moodle_password

secrets:
  moodle_password:
    file: ./secrets/moodle_password.txt   # en .gitignore
  moodle_username:
    file: ./secrets/moodle_username.txt
```

### 4.2 Patrón `_FILE` en MCPs

Los MCPs deben adoptar el patrón estándar `_FILE` para leer secretos desde archivo cuando la variable de entorno directa no está presente:

```python
def _load_secret(env_key: str) -> str:
    """Lee secreto desde env var o desde archivo (patrón _FILE)."""
    value = os.environ.get(env_key)
    if value:
        return value
    file_path = os.environ.get(f"{env_key}_FILE")
    if file_path:
        return Path(file_path).read_text().strip()
    raise EnvironmentError(f"Falta {env_key} o {env_key}_FILE")
```

Este patrón será implementado en todos los MCPs que manejen credenciales en Fase 2.

### 4.3 Migración desde keyring

```bash
# Export desde keyring (solo en desarrollo)
for key in MOODLE_PASSWORD MOODLE_USERNAME GITLAB_TOKEN; do
    keyring get lina "$key" > secrets/${key,,}.txt
done
```

Los archivos en `secrets/` están en `.gitignore`. Solo el mantenedor del deploy los genera.

---

## 5. Fase 3+: sops + age (opción preferida para single-node)

Para un despliegue más robusto sin infraestructura adicional, [sops](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age) permiten:

- Encriptar archivos de secretos en el repo (`.enc.yaml`).
- Desencriptar con la clave privada del operador (`age-keygen`).
- Integración nativa con CI/CD (la clave privada se almacena como GitHub Actions secret).

```bash
# Encriptar
sops --age $(cat ~/.config/sops/age/keys.txt | grep public | cut -d: -f2) \
     --encrypt secrets.yaml > secrets.enc.yaml

# Desencriptar en runtime
sops --decrypt secrets.enc.yaml | docker secret create - 
```

**Ventaja sobre Vault**: no requiere servidor adicional. Adecuado para LINA single-node.

---

## 6. Fase 4+: HashiCorp Vault / Infisical (descartado para Fase 2)

HashiCorp Vault y Infisical son herramientas de gestión de secretos para entornos multi-tenant o multi-servicio. Se considerarán si:

- LINA escala a más de una instancia.
- Se requiere rotación automática de credenciales.
- Se necesita audit trail centralizado de acceso a secretos.

**Rechazados para Fase 2**: overhead operacional no justificado para un despliegue personal single-node.

---

## 7. Consecuencias

### Positivas
- Ruta de migración clara: keyring → Docker secrets → sops → Vault.
- El patrón `_FILE` hace que los MCPs sean agnósticos al mecanismo de inyección.
- Los secretos nunca van en variables de entorno en el `Dockerfile` (que quedan en la imagen).

### Negativas / Trade-offs
- La migración a Docker secrets requiere modificar todos los MCPs para implementar `_load_secret()`.
- En Fase 0/1, los archivos `secrets/*.txt` en el host son texto plano (mitigado: `chmod 600`, solo en laptop personal).

---

## 8. Reglas que NO cambian

Independientemente de la fase:

1. **Nunca** commitear secretos en el repo (ni en `.env`, ni en `config.yaml`).
2. **Nunca** loguear secretos (ni en stderr ni en logs estructurados).
3. **Siempre** usar `_load_secret()` o equivalente para leer credenciales — nunca hardcoded.
4. El MCP `lina-secrets` es la fuente de verdad en Fase 0/1; los demás MCPs **piden** los secretos a él o los leen de `_FILE` según el ambiente.

---

## 9. Referencias

- [Docker secrets documentation](https://docs.docker.com/engine/swarm/secrets/)
- [sops + age tutorial](https://github.com/getsops/sops#encrypting-using-age)
- [12-Factor App — Config](https://12factor.net/config)
- ADR 0006 — Image Base Policy (contexto de containerización)

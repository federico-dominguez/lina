# ADR 0006 — Image Base Policy para MCPs Docker

**Status:** Accepted  
**Date:** 2025-01-27  
**Deciders:** Federico Dominguez  
**Tags:** docker, security, image, distroless

---

## 1. Contexto

Al containerizar los MCPs de LINA (Fase 2+), se debe elegir una imagen base para el runtime. Los objetivos son:

- **Superficie de ataque mínima**: menos paquetes = menos CVEs.
- **Imágenes pequeñas**: menor tiempo de pull, menor footprint en CI.
- **Compatibilidad con Python 3.12+**: los MCPs usan `uv` y paquetes del PyPI.
- **Reproducibilidad**: builds deterministas, sin cambios silenciosos.

Las alternativas principales para imágenes Python en producción son:

| Imagen                             | Tamaño (comprimido) | Shell | CVEs típicos | Notas                          |
|------------------------------------|---------------------|-------|--------------|--------------------------------|
| `python:3.12`                      | ~350 MB             | sí    | alto         | Build only                     |
| `python:3.12-slim`                 | ~50 MB              | sí    | medio        | Debian slim                    |
| `python:3.12-alpine`               | ~20 MB              | sh    | bajo         | musl libc, issues con wheels   |
| `gcr.io/distroless/python3-debian12` | ~25 MB           | no    | muy bajo     | No package manager, no shell   |
| `cgr.dev/chainguard/python`        | ~15 MB              | no    | mínimo       | Wolfi, daily rebuild, SBOMs    |

---

## 2. Decisión

**Usar builds multi-stage con `python:3.12-slim` para build y `gcr.io/distroless/python3-debian12` para runtime.**

La imagen de Chainguard (`cgr.dev/chainguard/python`) es técnicamente superior en superficie CVE, pero requiere suscripción para imágenes con etiquetas fijas en producción. Se adopta como alternativa futura.

---

## 3. Justificación

### 3.1 Por qué distroless > alpine para Python

Alpine usa `musl libc` en lugar de `glibc`. Muchos paquetes Python distribuyen wheels compilados contra `glibc`. Usar Alpine implica compilar todo desde source o usar wheels `musllinux`, lo que:
- Aumenta el tiempo de build
- Puede fallar silenciosamente con paquetes C (numpy, cryptography, etc.)
- No aplica para mcps que usen `keyring` (que linkea libsecret)

Distroless usa `glibc` (Debian 12 base) — compatible con todos los wheels de PyPI.

### 3.2 Por qué distroless mejora la postura de seguridad

Distroless elimina:
- `/bin/sh`, `bash`, `busybox` — sin shell injection posible
- `apt`, `pip` — sin instalación de paquetes en runtime
- Herramientas de debugging que un atacante podría explotar (curl, wget, nc)

El resultado: si un atacante explota una vulnerabilidad en el MCP, tiene un entorno minimalista para moverse lateralmente.

### 3.3 Multi-stage build

```dockerfile
# Build stage: instala deps con uv
FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --no-dev --frozen

# Runtime stage: distroless, solo los artefactos
FROM gcr.io/distroless/python3-debian12:nonroot
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "lina_secrets.server"]
```

El tag `:nonroot` corre el proceso con UID 65532 (nobody) — mínimos privilegios.

---

## 4. Consecuencias

### Positivas
- Reducción estimada del 90%+ de CVEs comparado con `python:3.12-slim` puro.
- Imágenes de runtime ~25-30 MB (vs ~120 MB con slim).
- Cumple con principio de mínimo privilegio (no-root por defecto).
- Compatible con scanners de vulnerabilidades estándar (Trivy, Grype).

### Negativas / Trade-offs
- **Sin shell en runtime**: debugging en contenedor requiere ephemeral debug containers (`kubectl debug` o `docker run --rm -it --pid=container`). Para LINA en laptop, esto es aceptable.
- **Sin `apt` en runtime**: actualizaciones de sistema requieren rebuild de la imagen, no `apt-get update`. Es el comportamiento correcto — inmutabilidad.
- **Digest pinning**: para reproducibilidad máxima, usar `gcr.io/distroless/python3-debian12@sha256:...` en lugar de tag flotante. Se revisará en Fase 2.

---

## 5. Alternativas rechazadas

### `python:3.12-alpine`
Rechazado por incompatibilidad de wheels `glibc` con `musl libc`. El MCP `lina-secrets` usa `keyring` que requiere bindings nativos compilados.

### `cgr.dev/chainguard/python`
Técnicamente superior (CVE daily rebuild, SBOMs incluidos). Rechazado para Fase 2 por requerir suscripción para uso en producción con imágenes fijadas. Se reconsiderará en Fase 3+ si el proyecto escala.

### Sin containerizar (ejecutar desde host)
Rechazado para producción. Aceptable solo en Fase 0/1 (desarrollo local con systemd).

---

## 6. Referencias

- [Distroless images — Google](https://github.com/GoogleContainerTools/distroless)
- [Chainguard Python](https://edu.chainguard.dev/chainguard/chainguard-images/reference/python/)
- [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
- ADR 0005 — MCP Transport (contexto de Fase 2+)

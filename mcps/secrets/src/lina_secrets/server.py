"""LINA MCP secrets — interfaz al system-keyring o backend de archivos.

Tools expuestos:
    - secret_get(service, key)        → valor o error si no existe
    - secret_set(service, key, value) → guarda y devuelve confirmación
    - secret_delete(service, key)
    - secret_list(service)            → lista de keys conocidas (best-effort)
    - keyring_backend()               → informa el backend en uso

Backends:
    keyring (default): usa system-keyring (libsecret/kwallet) — modo host.
    file:             usa archivos en LINA_SECRETS_FILE_DIR — modo container.

Variables de entorno:
    LINA_SECRETS_BACKEND    keyring | file  (default: keyring)
    LINA_SECRETS_FILE_DIR   directorio para el backend file (default: /run/secrets/lina)
    MCP_TRANSPORT           stdio | sse | streamable-http  (default: stdio)
    MCP_PORT                puerto HTTP (default: 8000, solo en modo no-stdio)
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import keyring
import keyring.errors
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-secrets] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-secrets")

NAMESPACE = os.environ.get("LINA_KEYRING_SERVICE", "lina")
_INDEX_KEY = "__index__"  # CSV de keys conocidas por service, para `secret_list`.

# ─── transport (Fase 2: container mode) ───────────────────────────────────────
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

# ─── backend de secretos ──────────────────────────────────────────────────────
# keyring = system-keyring (default, host mode)
# file    = archivos en LINA_SECRETS_FILE_DIR (container mode / Docker secrets)
BACKEND = os.environ.get("LINA_SECRETS_BACKEND", "keyring")
_VALID_BACKENDS = {"keyring", "file"}
if BACKEND not in _VALID_BACKENDS:
    raise ValueError(
        f"LINA_SECRETS_BACKEND inválido: {BACKEND!r} — valores soportados: {_VALID_BACKENDS}"
    )
FILE_DIR = Path(os.environ.get("LINA_SECRETS_FILE_DIR", "/run/secrets/lina"))

mcp = FastMCP("lina-secrets", host="0.0.0.0", port=_MCP_HTTP_PORT)


def _svc(service: str) -> str:
    service = service.strip()
    if not service or "/" in service or "\0" in service:
        raise ValueError(f"service inválido: {service!r}")
    return f"{NAMESPACE}:{service}"


def _index_get(service: str) -> set[str]:
    raw = keyring.get_password(_svc(service), _INDEX_KEY) or ""
    return {k for k in raw.split(",") if k}


def _index_add(service: str, key: str) -> None:
    keys = _index_get(service) | {key}
    keyring.set_password(_svc(service), _INDEX_KEY, ",".join(sorted(keys)))


def _index_remove(service: str, key: str) -> None:
    keys = _index_get(service) - {key}
    if keys:
        keyring.set_password(_svc(service), _INDEX_KEY, ",".join(sorted(keys)))
    else:
        try:
            keyring.delete_password(_svc(service), _INDEX_KEY)
        except keyring.errors.PasswordDeleteError:
            pass


# ─── file backend helpers ─────────────────────────────────────────────────────


_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_name(name: str, label: str) -> None:
    """Valida que service/key no contengan caracteres peligrosos (path traversal)."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"{label} inválido: {name!r} — solo se permiten [A-Za-z0-9._-] (1-64 chars)"
        )


def _file_path(service: str, key: str) -> Path:
    """Ruta canónica para un secreto en el backend file.

    Valida service y key para prevenir path traversal, luego resuelve y
    verifica que la ruta final esté dentro de FILE_DIR.
    """
    _validate_name(service, "service")
    _validate_name(key, "key")
    # Separador __ evita colisiones entre service=a,key=b_c y service=a_b,key=c.
    p = (FILE_DIR / f"{service}__{key}").resolve()
    if not str(p).startswith(str(FILE_DIR.resolve()) + "/") and p != FILE_DIR.resolve():
        raise ValueError(f"ruta fuera de FILE_DIR: {p}")
    return p


def _file_get(service: str, key: str) -> str | None:
    p = _file_path(service, key)
    if not p.exists():
        return None
    # Eliminar solo el newline final (común en Docker secrets), no strip completo.
    content = p.read_text(encoding="utf-8")
    return content.rstrip("\n")


def _file_set(service: str, key: str, value: str) -> None:
    FILE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    p = _file_path(service, key)
    # Escritura atómica: crear tmp con permisos 0600 desde el inicio, luego
    # os.replace() (atómico en POSIX). Evita la ventana race entre write y chmod.
    fd, tmp_path = tempfile.mkstemp(dir=FILE_DIR)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        os.replace(tmp_path, p)
    except:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _file_delete(service: str, key: str) -> None:
    p = _file_path(service, key)
    if p.exists():
        p.unlink()


def _file_list(service: str) -> list[str]:
    if not FILE_DIR.exists():
        return []
    prefix = f"{service}__"
    return sorted(p.name[len(prefix) :] for p in FILE_DIR.iterdir() if p.name.startswith(prefix))


@mcp.tool()
def secret_get(service: str, key: str) -> str:
    """Obtiene el valor de un secreto.

    Args:
        service: agrupador lógico (ej. 'moodle', 'gmail', 'deepseek').
        key:     nombre del campo (ej. 'MOODLE_PASSWORD', 'api_key').
    """
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    if BACKEND == "file":
        value = _file_get(service, key)
    else:
        value = keyring.get_password(_svc(service), key)
    if value is None:
        raise KeyError(f"no existe secreto {service}:{key}")
    log.info("get %s:%s (%d bytes) [%s]", service, key, len(value), BACKEND)
    return value


@mcp.tool()
def secret_set(service: str, key: str, value: str) -> str:
    """Guarda (o sobreescribe) un secreto."""
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    if not value:
        raise ValueError("value vacío")
    if BACKEND == "file":
        _file_set(service, key, value)
    else:
        keyring.set_password(_svc(service), key, value)
        _index_add(service, key)
    log.info("set %s:%s (%d bytes) [%s]", service, key, len(value), BACKEND)
    return f"ok: guardado {service}:{key}"


@mcp.tool()
def secret_delete(service: str, key: str) -> str:
    """Elimina un secreto."""
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    if BACKEND == "file":
        if _file_get(service, key) is None:
            raise KeyError(f"no existe secreto {service}:{key}")
        _file_delete(service, key)
    else:
        try:
            keyring.delete_password(_svc(service), key)
        except keyring.errors.PasswordDeleteError as e:
            raise KeyError(f"no existe secreto {service}:{key}") from e
        _index_remove(service, key)
    log.info("delete %s:%s [%s]", service, key, BACKEND)
    return f"ok: eliminado {service}:{key}"


@mcp.tool()
def secret_list(service: str) -> list[str]:
    """Lista las keys conocidas para un service."""
    if BACKEND == "file":
        return _file_list(service)
    return sorted(_index_get(service))


@mcp.tool()
def keyring_backend() -> str:
    """Devuelve el backend en uso (debugging)."""
    if BACKEND == "file":
        return f"file:{FILE_DIR}"
    return (
        f"{keyring.get_keyring().__class__.__module__}.{keyring.get_keyring().__class__.__name__}"
    )


def main() -> None:
    log.info(
        "starting (namespace=%s, backend=%s, transport=%s)",
        NAMESPACE,
        BACKEND,
        _MCP_TRANSPORT,
    )
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

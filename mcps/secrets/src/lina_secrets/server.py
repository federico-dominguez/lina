"""LINA MCP secrets — interfaz al system-keyring.

Tools expuestos:
    - secret_get(service, key)        → valor o error si no existe
    - secret_set(service, key, value) → guarda y devuelve confirmación
    - secret_delete(service, key)
    - secret_list(service)            → lista de keys conocidas (best-effort)

Convención de nombres:
    El MCP escribe bajo el "service" `<LINA_KEYRING_SERVICE>:<service>` para
    no contaminar el namespace global del keyring. `LINA_KEYRING_SERVICE`
    por defecto = "lina".
"""

from __future__ import annotations

import logging
import os
import sys

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

mcp = FastMCP("lina-secrets")


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


@mcp.tool()
def secret_get(service: str, key: str) -> str:
    """Obtiene el valor de un secreto del keyring.

    Args:
        service: agrupador lógico (ej. 'moodle', 'gmail', 'deepseek').
        key:     nombre del campo (ej. 'MOODLE_PASSWORD', 'api_key').
    """
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    value = keyring.get_password(_svc(service), key)
    if value is None:
        raise KeyError(f"no existe secreto {service}:{key}")
    log.info("get %s:%s (%d bytes)", service, key, len(value))
    return value


@mcp.tool()
def secret_set(service: str, key: str, value: str) -> str:
    """Guarda (o sobreescribe) un secreto en el keyring."""
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    if not value:
        raise ValueError("value vacío")
    keyring.set_password(_svc(service), key, value)
    _index_add(service, key)
    log.info("set %s:%s (%d bytes)", service, key, len(value))
    return f"ok: guardado {service}:{key}"


@mcp.tool()
def secret_delete(service: str, key: str) -> str:
    """Elimina un secreto del keyring."""
    if key == _INDEX_KEY:
        raise ValueError("key reservado")
    try:
        keyring.delete_password(_svc(service), key)
    except keyring.errors.PasswordDeleteError as e:
        raise KeyError(f"no existe secreto {service}:{key}") from e
    _index_remove(service, key)
    log.info("delete %s:%s", service, key)
    return f"ok: eliminado {service}:{key}"


@mcp.tool()
def secret_list(service: str) -> list[str]:
    """Lista las keys conocidas para un service (vía índice mantenido por este MCP)."""
    return sorted(_index_get(service))


@mcp.tool()
def keyring_backend() -> str:
    """Devuelve el backend de keyring en uso (debugging)."""
    return f"{keyring.get_keyring().__class__.__module__}.{keyring.get_keyring().__class__.__name__}"


def main() -> None:
    log.info("starting (namespace=%s, backend=%s)", NAMESPACE, keyring_backend())
    mcp.run()


if __name__ == "__main__":
    main()

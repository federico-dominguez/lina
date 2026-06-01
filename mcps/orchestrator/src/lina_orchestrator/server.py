"""LINA Orchestrator MCP — expone políticas de subagentes al manager.

Esta es la versión MVP (issue #85). Implementa solo las tools de consulta de
políticas (read-side del PolicyStore). Las tools de gestión de subagentes
(spawn_subagent, kill_agent, ...) se agregarán cuando #83 (schema SQL) y #84
(spawner goosed-per-subagent) estén disponibles.

Tools expuestas:
    - list_roles() → [str]
    - get_role(role) → dict
    - check_mcp_allowed(role, mcp_name) → bool
    - check_requires_approval(role, action) → bool
    - reload_policies() → {success, roles_count, roles}

Cargado desde:
    - $LINA_POLICIES_FILE o /home/user/lina/config/policies.yaml por defecto.

Reload:
    - SIGHUP al proceso o tool reload_policies() recargan policies.yaml.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from lina_orchestrator.application.policy_service import PolicyService
from lina_orchestrator.domain.policy import PolicyStore

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-orchestrator] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-orchestrator")

_DEFAULT_POLICIES = Path("/home/user/lina/config/policies.yaml")
_POLICIES_PATH = Path(os.environ.get("LINA_POLICIES_FILE", str(_DEFAULT_POLICIES)))
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))


def _build_service() -> PolicyService:
    """Carga policies.yaml y devuelve un PolicyService listo para servir.

    Si el archivo no existe, falla rápido (no se puede arrancar un orquestador
    sin políticas).
    """
    if not _POLICIES_PATH.exists():
        raise FileNotFoundError(
            f"policies.yaml no encontrado en {_POLICIES_PATH}. "
            "Configurá LINA_POLICIES_FILE o copiá config/policies.yaml al path esperado."
        )
    store = PolicyStore.from_yaml(_POLICIES_PATH)
    log.info("policies cargadas desde %s — %d roles", _POLICIES_PATH, len(store.roles()))
    return PolicyService(store)


mcp = FastMCP("lina-orchestrator", host="0.0.0.0", port=_MCP_HTTP_PORT)  # noqa: S104 — bind 0.0.0.0 intencional para contenedor
_service: PolicyService | None = None


def _svc() -> PolicyService:
    """Lazy init del PolicyService (permite import sin file)."""
    global _service
    if _service is None:
        _service = _build_service()
    return _service


# ─── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def list_roles() -> list[str]:
    """Devuelve los nombres de rol definidos en policies.yaml.

    Ejemplo:
        ["dev", "ops", "research", "study"]
    """
    return _svc().list_roles()


@mcp.tool()
def get_role(role: str) -> dict:
    """Devuelve la política completa de un rol.

    Argumentos:
        role: nombre del rol (ej. "dev", "study").

    Retorna:
        Dict con allowed_mcps, sudo_allowed, sudo_allowlist, límites de runtime,
        tokens y costo diario, y lista de acciones que requieren aprobación.

    Errores:
        Rol desconocido → {"error": "Rol 'X' no definido...", "known_roles": [...]}.
    """
    try:
        return dict(_svc().get_role(role))
    except KeyError as exc:
        return {
            "error": str(exc),
            "known_roles": _svc().list_roles(),
        }


@mcp.tool()
def check_mcp_allowed(role: str, mcp_name: str) -> dict:
    """¿El rol tiene permiso para usar el MCP indicado?

    Argumentos:
        role:     nombre del rol.
        mcp_name: nombre del MCP (ej. "lina-fs-safe", "lina-shell-policy").

    Retorna:
        {"role": ..., "mcp": ..., "allowed": bool}
        Rol desconocido → allowed=False (fail-closed).
    """
    allowed = _svc().check_mcp_allowed(role, mcp_name)
    return {"role": role, "mcp": mcp_name, "allowed": allowed}


@mcp.tool()
def check_requires_approval(role: str, action: str) -> dict:
    """¿La acción requiere aprobación humana antes de ejecutarse?

    Argumentos:
        role:   nombre del rol.
        action: identificador de la acción (ej. "modify_file", "git_push").

    Retorna:
        {"role": ..., "action": ..., "requires_approval": bool}
        Rol desconocido → requires_approval=True (fail-closed: pedir aprobación es seguro).
    """
    requires = _svc().check_requires_approval(role, action)
    return {"role": role, "action": action, "requires_approval": requires}


@mcp.tool()
def reload_policies() -> dict:
    """Recarga policies.yaml desde disco sin reiniciar el proceso.

    Retorna:
        {"success": True, "roles_count": N, "roles": [...]} en éxito.
        {"success": False, "error": "..."} si la recarga falla
        (archivo borrado, YAML inválido, schema roto).
    """
    return _svc().reload()


def main() -> None:
    log.info(
        "starting (transport=%s, policies=%s)",
        _MCP_TRANSPORT,
        _POLICIES_PATH,
    )
    # Eager init para fallar rápido si policies.yaml está mal.
    _svc()
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

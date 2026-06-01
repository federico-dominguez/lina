"""Application service: política de acceso para subagentes.

Esta capa expone una API mínima sobre el PolicyStore para que el server MCP
(o futuros consumidores) consulten políticas sin acoplarse al modelo Pydantic.

Mantiene la dependencia hacia el dominio (RolePolicy, PolicyStore) y los
adapta a tipos primitivos serializables (dict, list, bool) listos para JSON.
"""

from __future__ import annotations

from typing import TypedDict

from lina_orchestrator.domain.policy import PolicyStore, RolePolicy


class RoleSummary(TypedDict):
    """Vista serializable de una RolePolicy."""

    role: str
    description: str
    allowed_mcps: list[str]
    sudo_allowed: bool
    sudo_allowlist: list[str]
    max_runtime_minutes: int
    max_tokens_per_run: int
    max_usd_per_day: float
    needs_approval_for: list[str]


def _to_summary(role: str, policy: RolePolicy) -> RoleSummary:
    return RoleSummary(
        role=role,
        description=policy.description,
        allowed_mcps=list(policy.allowed_mcps),
        sudo_allowed=policy.sudo_allowed,
        sudo_allowlist=list(policy.sudo_allowlist),
        max_runtime_minutes=policy.max_runtime_minutes,
        max_tokens_per_run=policy.max_tokens_per_run,
        max_usd_per_day=policy.max_usd_per_day,
        needs_approval_for=list(policy.needs_approval_for),
    )


class PolicyService:
    """API estable para consulta de políticas — pensada para servidores MCP."""

    def __init__(self, store: PolicyStore) -> None:
        self._store = store

    def list_roles(self) -> list[str]:
        """Roles definidos en policies.yaml."""
        return sorted(self._store.roles())

    def get_role(self, role: str) -> RoleSummary:
        """Resumen completo de un rol.

        Raises:
            KeyError: rol desconocido (fail-closed delegado al store).
        """
        policy = self._store.get(role)
        return _to_summary(role, policy)

    def check_mcp_allowed(self, role: str, mcp_name: str) -> bool:
        """¿El rol tiene permiso para usar `mcp_name`? Rol desconocido → False."""
        try:
            policy = self._store.get(role)
        except KeyError:
            return False
        return policy.allows_mcp(mcp_name)

    def check_requires_approval(self, role: str, action: str) -> bool:
        """¿La acción requiere aprobación humana para este rol?

        Rol desconocido → True (fail-closed: pedir aprobación es la opción segura).
        """
        try:
            policy = self._store.get(role)
        except KeyError:
            return True
        return policy.requires_approval(action)

    def reload(self) -> dict:
        """Recarga policies.yaml desde disco.

        Returns:
            {"success": True, "roles_count": N, "roles": [...]}
            o {"success": False, "error": "..."} si la recarga falla.
        """
        try:
            self._store.reload()
        except Exception as exc:  # noqa: BLE001 — exponer cualquier error de carga
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        roles = self.list_roles()
        return {"success": True, "roles_count": len(roles), "roles": roles}

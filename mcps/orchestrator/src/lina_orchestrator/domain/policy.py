"""Policy domain model — LINA Orchestrator.

Carga y valida `config/policies.yaml`. Fail-closed: rol desconocido → error.

Uso básico::

    policy_store = PolicyStore.from_yaml(Path("config/policies.yaml"))
    role_policy = policy_store.get("dev")  # RolePolicy
    policy_store.reload()                   # re-lee desde disco (para SIGHUP)
"""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, model_validator

# ─── Value objects ────────────────────────────────────────────────────────────


class RolePolicy(BaseModel):
    """Política de seguridad para un rol de subagente.

    Inmutable tras construcción — los cambios llegan via PolicyStore.reload().
    """

    model_config = {"frozen": True}

    description: str = ""

    allowed_mcps: list[str] = Field(default_factory=list)
    """MCPs a los que puede conectarse este rol. Deny-all el resto."""

    sudo_allowed: bool = False
    sudo_allowlist: list[str] = Field(default_factory=list)

    max_runtime_minutes: Annotated[int, Field(gt=0)] = 15
    max_tokens_per_run: Annotated[int, Field(gt=0)] = 20000
    max_usd_per_day: Annotated[float, Field(ge=0)] = 0.0
    """0 significa sin límite diario."""

    needs_approval_for: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sudo_requires_allowlist(self) -> RolePolicy:
        if self.sudo_allowed and not self.sudo_allowlist:
            raise ValueError("sudo_allowed=true requiere sudo_allowlist no vacío")
        return self

    def allows_mcp(self, mcp_name: str) -> bool:
        """Devuelve True si el rol tiene permiso para usar `mcp_name`."""
        return mcp_name in self.allowed_mcps

    def requires_approval(self, action: str) -> bool:
        """Devuelve True si la acción requiere confirmación humana."""
        return action in self.needs_approval_for


class PoliciesFile(BaseModel):
    """Estructura completa del archivo policies.yaml."""

    version: str
    roles: dict[str, RolePolicy]

    @model_validator(mode="after")
    def _roles_not_empty(self) -> PoliciesFile:
        if not self.roles:
            raise ValueError("policies.yaml debe definir al menos un rol")
        return self


# ─── PolicyStore ──────────────────────────────────────────────────────────────


class PolicyStore:
    """Repositorio thread-safe de políticas, recargable sin restart.

    Example::

        store = PolicyStore.from_yaml(Path("config/policies.yaml"))
        store.register_sighup()   # reload en SIGHUP
        policy = store.get("dev") # RaisesKeyError si rol no existe
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: PoliciesFile = self._load()

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: Path) -> PolicyStore:
        """Crea y valida un PolicyStore desde el archivo dado.

        Raises:
            FileNotFoundError: si el archivo no existe.
            pydantic.ValidationError: si el contenido es inválido.
        """
        return cls(path)

    # ── public API ───────────────────────────────────────────────────────────

    def get(self, role: str) -> RolePolicy:
        """Devuelve la política del rol.

        Raises:
            KeyError: si el rol no está definido (fail-closed).
        """
        with self._lock:
            try:
                return self._data.roles[role]
            except KeyError:
                defined = sorted(self._data.roles.keys())
                raise KeyError(
                    f"Rol '{role}' no definido en policies.yaml. Roles disponibles: {defined}"
                ) from None

    def roles(self) -> list[str]:
        """Lista de roles definidos."""
        with self._lock:
            return list(self._data.roles.keys())

    def reload(self) -> None:
        """Re-lee policies.yaml desde disco y reemplaza el store atómicamente."""
        new_data = self._load()
        with self._lock:
            self._data = new_data

    def register_sighup(self) -> None:
        """Instala un handler SIGHUP que llama reload()."""

        def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
            self.reload()

        signal.signal(signal.SIGHUP, _handler)

    # ── private ──────────────────────────────────────────────────────────────

    def _load(self) -> PoliciesFile:
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return PoliciesFile.model_validate(raw)

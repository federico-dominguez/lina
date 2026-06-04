"""LINA Orchestrator MCP — políticas de subagentes y gestión del ciclo de vida.

Tools expuestas:
    Consulta de políticas (read-side):
    - list_roles() → [str]
    - get_role(role) → dict
    - check_mcp_allowed(role, mcp_name) → bool
    - check_requires_approval(role, action) → bool
    - reload_policies() → {success, roles_count, roles}

    Gestión de sub-agentes (issue #84+#85):
    - spawn_agent(role, goal) → {agent_id, pid, status, config_path}
    - kill_agent(agent_id) → {killed, message}
    - get_agent_status(agent_id) → {status, pid, elapsed_seconds, process_alive}
    - list_agents(include_completed?) → [{agent_id, role, goal, status, ...}]
    - send_instruction(agent_id, text) → {command_id, sent_at}

Cargado desde:
    - $LINA_POLICIES_FILE o /home/user/lina/config/policies.yaml por defecto.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from lina_orchestrator.application.policy_service import PolicyService
from lina_orchestrator.domain.policy import PolicyStore
from lina_orchestrator.application.lifecycle import LifecycleService
from lina_orchestrator.infrastructure.spawner import SpawnerService

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
_spawner: SpawnerService | None = None
_lifecycle: LifecycleService | None = None


def _svc() -> PolicyService:
    """Lazy init del PolicyService (permite import sin file)."""
    global _service
    if _service is None:
        _service = _build_service()
    return _service


def _spw() -> SpawnerService:
    """Lazy init del SpawnerService."""
    global _spawner
    if _spawner is None:
        _spawner = SpawnerService(_svc())
    return _spawner


def _lfc() -> LifecycleService:
    """Lazy init del LifecycleService."""
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = LifecycleService(_svc())
    return _lifecycle


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


# ─── Agent lifecycle tools (issues #84, #85) ─────────────────────────────────


@mcp.tool()
def spawn_agent(role: str, goal: str) -> dict:
    """Lanza un sub-agente goosed con los MCPs del rol indicado.

    El sub-agente recibe `goal` como instrucción inicial y trabaja de forma
    autónoma. LINA principal puede monitorear su progreso con get_agent_status()
    o enviarle instrucciones adicionales con send_instruction().

    Argumentos:
        role: rol del sub-agente — debe existir en policies.yaml
              (ej. "dev", "ops", "study", "research").
        goal: instrucción inicial para el sub-agente (texto libre).

    Retorna:
        {agent_id, role, goal, pid, status, config_path}
        Errores: {"error": "..."} si el rol es inválido o goosed no está disponible.
    """
    try:
        return _spw().spawn(role, goal)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def kill_agent(agent_id: str) -> dict:
    """Termina un sub-agente en ejecución (SIGTERM → SIGKILL).

    Argumentos:
        agent_id: UUID del agente (devuelto por spawn_agent).

    Retorna:
        {agent_id, killed: bool, message: str}
    """
    return _spw().kill(agent_id)


@mcp.tool()
def get_agent_status(agent_id: str) -> dict:
    """Devuelve el estado actual de un sub-agente.

    Combina el estado en lina-db con el estado real del proceso (poll).

    Argumentos:
        agent_id: UUID del agente.

    Retorna:
        {agent_id, role, goal, status, pid, elapsed_seconds, process_alive}
        status: pending | running | completed | failed | killed | timeout
    """
    return _spw().get_status(agent_id)


@mcp.tool()
def list_agents(include_completed: bool = False) -> list[dict]:
    """Lista los sub-agentes y su estado.

    Argumentos:
        include_completed: si True, incluye también los agentes que ya terminaron
                           (completed / failed / killed / timeout). Default False.

    Retorna:
        Lista de {agent_id, role, goal, status, pid, elapsed_seconds}.
    """
    try:
        import psycopg2
        import psycopg2.extras

        from lina_orchestrator.infrastructure.spawner import _db_conn  # noqa: PLC0415

        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if include_completed:
                    cur.execute(
                        """SELECT id AS agent_id, role, goal, status, pid,
                                  EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed_seconds
                           FROM agent_sessions
                           ORDER BY created_at DESC LIMIT 50"""
                    )
                else:
                    cur.execute(
                        """SELECT id AS agent_id, role, goal, status, pid, elapsed_seconds
                           FROM agent_sessions_active"""
                    )
                rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        return rows
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


@mcp.tool()
def send_instruction(agent_id: str, text: str) -> dict:
    """Envía una instrucción adicional a un sub-agente en ejecución.

    La instrucción se escribe en la tabla agent_commands. El sub-agente
    la leerá por polling o via LISTEN/NOTIFY de PostgreSQL.

    Argumentos:
        agent_id: UUID del agente destino.
        text:     instrucción en texto libre.

    Retorna:
        {command_id, agent_id, kind, sent_at}
    """
    try:
        import json as _json  # noqa: PLC0415

        import psycopg2.extras as _pge  # noqa: PLC0415

        from lina_orchestrator.infrastructure.spawner import (  # noqa: PLC0415
            _db_append_event,
            _db_conn,
        )

        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=_pge.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO agent_commands (session_id, kind, args_json)"
                    " VALUES (%s, %s, %s) RETURNING id, sent_at",
                    (agent_id, "send_instruction", _json.dumps({"text": text})),
                )
                row = dict(cur.fetchone())
            conn.commit()
        finally:
            conn.close()

        _db_append_event(agent_id, "instruction_received", {"text": text[:100]})

        return {
            "command_id": row["id"],
            "agent_id": agent_id,
            "kind": "send_instruction",
            "sent_at": row["sent_at"].isoformat() if row.get("sent_at") else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@mcp.tool()
def list_dead_letter(limit: int = 20) -> list[dict]:
    """Lista los sub-agentes en la dead-letter queue.

    Agentes que fallaron >3 veces y no serán reintentados automáticamente.
    Útil para monitoreo y diagnóstico.

    Argumentos:
        limit: máximo de resultados (default 20).

    Retorna:
        Lista de {session_id, role, goal, retry_count, last_error, moved_at}.
    """
    return _lfc().list_dead_letter(limit)


@mcp.tool()
def get_lifecycle_status() -> dict:
    """Estado del watchdog de sub-agentes.

    Retorna:
        {"watchdog_running": bool, "watchdog_interval": int, "max_retries": int}
    """
    return {
        "watchdog_running": _lfc().is_running,
        "watchdog_interval": 30,
        "max_retries": 3,
        "backoff_intervals": [60, 300, 900],
    }


def main() -> None:
    log.info(
        "starting (transport=%s, policies=%s)",
        _MCP_TRANSPORT,
        _POLICIES_PATH,
    )
    # Eager init para fallar rápido si policies.yaml está mal.
    _svc()
    # Arrancar watchdog de sub-agentes (issue #89)
    try:
        _lfc().start()
    except Exception:  # noqa: BLE001
        log.exception("failed to start lifecycle watchdog — continuing without it")
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()

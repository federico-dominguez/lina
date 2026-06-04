"""SpawnerService — lanza sub-agentes goosed como procesos hijos (issue #84).

Responsabilidades:
  1. Generar una config temporal de goosed filtrada por las políticas del rol.
  2. Lanzar el proceso goosed hijo con esa config.
  3. Persistir el session_id y PID en lina-db (via lina-db MCP o directo a DB).
  4. Proveer kill / status para gestión del ciclo de vida.

Diseño (MVP sin Temporal):
  - subprocess.Popen — simple, sin dependencias extra.
  - Config temporal en /tmp/lina-agent-<session_id>.yaml — borrada al kill/end.
  - lina-db como control-plane: create_agent_session / update_agent_status.
  - SIGTERM → espera _KILL_GRACE_SECONDS → SIGKILL.

Para usar dentro del contenedor lina-mcp-orchestrator, el binario `goosed`
debe estar en PATH o en GOOSED_BIN_PATH. En el entorno de desarrollo local
se puede pasar la ruta explícita via LINA_GOOSED_BIN.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
import yaml

log = logging.getLogger("lina-orchestrator.spawner")

# ─── Constantes de entorno ────────────────────────────────────────────────────

# Ruta al binario goosed (fallback: goosed en PATH)
_GOOSED_BIN = os.environ.get("LINA_GOOSED_BIN", "goosed")

# URL de PostgreSQL para control-plane (reutiliza la del lina-db MCP)
_DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

# Config base de goosed (la del container, que contiene todos los MCPs)
_BASE_CONFIG = Path(
    os.environ.get(
        "LINA_BASE_GOOSE_CONFIG",
        "/home/user/lina/config/goose.config.docker.yaml",
    )
)

# Directorio donde se guardan los configs temporales de sub-agentes
_AGENT_CONFIG_DIR = Path(os.environ.get("LINA_AGENT_CONFIG_DIR", "/tmp/lina-agents"))  # noqa: S108

# Segundos de gracia entre SIGTERM y SIGKILL
_KILL_GRACE_SECONDS = int(os.environ.get("LINA_KILL_GRACE_SECONDS", "5"))

# MCPs built-in que siempre se incluyen (independiente del rol)
_BUILTIN_EXTENSIONS = {
    "developer",
    "computercontroller",
    "memory",
    "todo",
    "extensionmanager",
    "analyze",
    "summon",
    "summarize",
}

# Mapeo nombre-policy → nombre-extension en goose.config.docker.yaml
_MCP_TO_EXTENSION_KEY: dict[str, str] = {
    "lina-secrets": "lina-secrets",
    "lina-fs-safe": "lina-fs-safe",
    "lina-shell-policy": "lina-shell-policy",
    "lina-systemd-user": "lina-systemd-user",
    "lina-moodle": "lina-moodle",
    "lina-db": "lina-db",
    "lina-github": "lina-github",
    "lina-gitlab": "lina-gitlab",
    "lina-gcalendar": "lina-gcalendar",
    "lina-gns3": "lina-gns3",
    "lina-orchestrator": "lina-orchestrator",
}


# ─── DB helpers (directo, sin MCP — el spawner está dentro del orchestrator) ─


def _db_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(_DB_URL)


def _db_create_session(session_id: str, role: str, goal: str) -> None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_sessions (id, role, goal) VALUES (%s, %s, %s)",
                (session_id, role, goal),
            )
        conn.commit()
    finally:
        conn.close()


def _db_update_status(
    session_id: str,
    status: str,
    *,
    pid: int | None = None,
    config_path: str | None = None,
    result_summary: str | None = None,
) -> None:
    started_sql = ", started_at = NOW()" if status == "running" else ""
    ended_sql = (
        ", ended_at = NOW()" if status in {"completed", "failed", "killed", "timeout"} else ""
    )
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE agent_sessions
                    SET status = %s,
                        pid = COALESCE(%s, pid),
                        config_path = COALESCE(%s, config_path),
                        result_summary = COALESCE(%s, result_summary),
                        updated_at = NOW()
                        {started_sql}{ended_sql}
                    WHERE id = %s""",  # noqa: S608
                (status, pid, config_path, result_summary, session_id),
            )
        conn.commit()
    finally:
        conn.close()


def _db_append_event(session_id: str, kind: str, payload: dict | None = None) -> None:
    import json

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_events (session_id, kind, payload_json) VALUES (%s, %s, %s)",
                (session_id, kind, json.dumps(payload or {})),
            )
        conn.commit()
    finally:
        conn.close()


# ─── Config generation ────────────────────────────────────────────────────────


def _generate_agent_config(session_id: str, role: str, allowed_mcps: list[str]) -> Path:
    """Genera una config temporal de goosed con solo los MCPs permitidos para el rol.

    Lee la config base del container y filtra las extensiones según allowed_mcps.
    Los built-ins siempre se incluyen.

    Returns:
        Path al archivo YAML temporal creado.
    """
    _AGENT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not _BASE_CONFIG.exists():
        raise FileNotFoundError(
            f"Config base de goosed no encontrada: {_BASE_CONFIG}. "
            "Configurá LINA_BASE_GOOSE_CONFIG."
        )

    with _BASE_CONFIG.open() as f:
        base = yaml.safe_load(f)

    # Filtrar extensiones: mantener built-ins + las del rol
    base_extensions: dict[str, Any] = base.get("extensions", {})
    allowed_ext_keys = _BUILTIN_EXTENSIONS | {
        _MCP_TO_EXTENSION_KEY.get(mcp, mcp) for mcp in allowed_mcps
    }

    filtered_extensions: dict[str, Any] = {
        k: v for k, v in base_extensions.items() if k in allowed_ext_keys
    }
    base["extensions"] = filtered_extensions

    # Añadir metadatos del sub-agente como comentario en YAML es complejo;
    # se usan env vars que goosed expone al agente.
    config_path = _AGENT_CONFIG_DIR / f"agent-{session_id}.yaml"
    with config_path.open("w") as f:
        yaml.dump(base, f, default_flow_style=False, allow_unicode=True)

    log.info(
        "config generada para %s (role=%s, mcps=%d, path=%s)",
        session_id,
        role,
        len(allowed_mcps),
        config_path,
    )
    return config_path


# ─── SpawnerService ───────────────────────────────────────────────────────────


# ─── Watchdog de timeout (issue #113) ──────────────────────────────────────────
# Thread daemon que cada _WATCHDOG_INTERVAL segundos revisa agentes running
# y mata los que excedieron max_runtime_minutes de la policy del rol.

_WATCHDOG_INTERVAL = 10  # segundos entre chequeos


def _enforce_timeout(
    session_id: str,
    role: str,
    pid: int,
    started_at: datetime,
    max_runtime_minutes: int,
) -> dict | None:
    """Verifica si un agente excedió su timeout y lo mata.

    Args:
        session_id: UUID del agente.
        role: rol del agente.
        pid: PID del proceso.
        started_at: timestamp de inicio.
        max_runtime_minutes: max permitido desde policies.yaml.

    Returns:
        Dict con resumen del timeout si se aplicó, None si está dentro del límite.
    """
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    max_seconds = max_runtime_minutes * 60

    if elapsed <= max_seconds:
        return None  # dentro del límite

    # Excedió → kill
    runtime_min = round(elapsed / 60, 1)
    log.warning(
        "TIMEOUT: session=%s role=%s pid=%d runtime=%.1fm max=%dm",
        session_id, role, pid, runtime_min, max_runtime_minutes,
    )

    # Matar proceso con killpg (propaga a hijos)
    try:
        os.killpg(pid, signal.SIGTERM)
        time.sleep(2)  # grace period
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # ya terminó
    except PermissionError:
        log.warning("killpg: permiso denegado para pid=%d", pid)

    # Actualizar DB
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE agent_sessions
                   SET status = 'timeout',
                       ended_at = NOW(),
                       updated_at = NOW(),
                       watchdog_note = %s
                   WHERE id = %s AND status = 'running'""",
                (f"timeout: excedió {max_runtime_minutes}m (runtime={runtime_min}m)", session_id),
            )
        conn.commit()
    finally:
        conn.close()

    # Evento estructurado para el gateway (issue #113)
    timeout_info = {
        "session_id": session_id,
        "role": role,
        "pid": pid,
        "runtime_min": runtime_min,
        "max_min": max_runtime_minutes,
        "message": f"⌛ Agente {session_id[:8]} timeout tras {runtime_min:.0f}m (max {max_runtime_minutes}m)",
    }
    _db_append_event(session_id, "timeout", timeout_info)
    log.info("TIMEOUT EVENT: %s", timeout_info["message"])
    return timeout_info


def _watchdog_loop(
    policy_service: Any,  # PolicyService (evita import circular)
    stop_event: threading.Event,
) -> None:
    """Loop del watchdog: cada _WATCHDOG_INTERVAL segundos chequea agentes running."""
    log.info("watchdog started (interval=%ds)", _WATCHDOG_INTERVAL)

    while not stop_event.is_set():
        try:
            conn = _db_conn()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """SELECT id, role, pid, started_at
                           FROM agent_sessions
                           WHERE status = 'running'
                             AND pid IS NOT NULL
                             AND started_at IS NOT NULL
                           LIMIT 50"""
                    )
                    running = cur.fetchall()
            finally:
                conn.close()

            for row in running:
                session_id = row["id"]
                role = row["role"]
                pid = row["pid"]
                started_at = row["started_at"]

                if not pid or not started_at:
                    continue

                # Obtener max_runtime_minutes de la policy
                try:
                    role_policy = policy_service.get_role(role)
                    max_minutes = role_policy.get("max_runtime_minutes", 120)
                except (KeyError, AttributeError):
                    max_minutes = 120  # fallback conservador

                _enforce_timeout(session_id, role, pid, started_at, max_minutes)

        except Exception:  # noqa: BLE001
            log.exception("watchdog error en ciclo")

        stop_event.wait(_WATCHDOG_INTERVAL)

    log.info("watchdog stopped")


class SpawnerService:
    """Gestiona el ciclo de vida de sub-agentes goosed.

    Uso:
        spawner = SpawnerService(policy_service)
        session = spawner.spawn("dev", "Crear PR con fix X")
        # ... más tarde ...
        spawner.kill(session["agent_id"])
    """

    def __init__(self, policy_service: Any) -> None:
        """
        Args:
            policy_service: instancia de PolicyService (para leer allowed_mcps).
        """
        self._policy = policy_service
        # Mapa en memoria: session_id → Popen; para poder hacer kill sin ir a DB
        self._processes: dict[str, subprocess.Popen] = {}
        # Watchdog thread (issue #113)
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    def start_watchdog(self) -> None:
        """Arranca el watchdog thread de timeout enforcement (issue #113).

        El watchdog monitorea agentes running cada 10s y mata automáticamente
        los que excedieron max_runtime_minutes. Daemon thread: no bloquea el
        shutdown del proceso.
        """
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            log.warning("watchdog thread ya está corriendo")
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(self._policy, self._watchdog_stop),
            name="watchdog-timeout",
            daemon=True,
        )
        self._watchdog_thread.start()
        log.info("watchdog: thread started (interval=%ds)", _WATCHDOG_INTERVAL)

    def stop_watchdog(self) -> None:
        """Detiene el watchdog thread."""
        self._watchdog_stop.set()
        log.info("watchdog: stop signal sent")

    def spawn(self, role: str, goal: str) -> dict:
        """Lanza un sub-agente goosed con la config filtrada para el rol.

        Flujo:
          1. Valida el rol contra policies.yaml.
          2. Genera un session_id UUID.
          3. Crea la sesión en lina-db (status=pending).
          4. Genera config temporal filtrada.
          5. Lanza goosed como subprocess.
          6. Actualiza la sesión en lina-db (status=running, pid=...).

        Args:
            role: rol del sub-agente (debe existir en policies.yaml).
            goal: instrucción inicial para el sub-agente.

        Returns:
            {agent_id, role, goal, pid, status, config_path}
        """
        # 1. Validar rol
        known = self._policy.list_roles()
        if role not in known:
            raise ValueError(f"Rol desconocido: {role!r}. Roles disponibles: {known}")

        role_policy = self._policy.get_role(role)
        allowed_mcps: list[str] = role_policy.get("allowed_mcps", [])

        # 2. Session ID
        session_id = uuid.uuid4().hex

        # 3. Crear en DB
        _db_create_session(session_id, role, goal)
        _db_append_event(session_id, "spawn_requested", {"role": role, "goal": goal[:200]})

        # 4. Generar config temporal
        config_path = _generate_agent_config(session_id, role, allowed_mcps)

        # 5. Lanzar proceso
        env = os.environ.copy()
        env["LINA_AGENT_SESSION_ID"] = session_id
        env["LINA_AGENT_ROLE"] = role

        cmd = [
            _GOOSED_BIN,
            "run",
            "--config",
            str(config_path),
        ]

        log.info("spawning agent %s (role=%s) cmd=%s", session_id, role, cmd)

        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            _db_update_status(session_id, "failed", result_summary=f"goosed not found: {exc}")
            _db_append_event(session_id, "error", {"error": str(exc)})
            raise RuntimeError(
                f"goosed no encontrado en {_GOOSED_BIN!r}. "
                "Configurá LINA_GOOSED_BIN con la ruta al binario."
            ) from exc

        # 6. Actualizar DB con PID
        self._processes[session_id] = proc
        _db_update_status(
            session_id,
            "running",
            pid=proc.pid,
            config_path=str(config_path),
        )
        _db_append_event(session_id, "process_started", {"pid": proc.pid})

        log.info("agent %s started (pid=%d)", session_id, proc.pid)
        return {
            "agent_id": session_id,
            "role": role,
            "goal": goal,
            "pid": proc.pid,
            "status": "running",
            "config_path": str(config_path),
        }

    def kill(self, agent_id: str) -> dict:
        """Termina un sub-agente enviando SIGTERM (luego SIGKILL si no responde).

        Args:
            agent_id: session_id del agente a terminar.

        Returns:
            {"agent_id": str, "killed": bool, "message": str}
        """
        proc = self._processes.get(agent_id)

        if proc is None:
            # Intento via DB: puede que el proceso haya sido spawneado en otra
            # instancia del servicio (restart del contenedor). Intentamos por PID.
            conn = _db_conn()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT pid, status FROM agent_sessions WHERE id = %s",
                        (agent_id,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()

            if not row:
                return {"agent_id": agent_id, "killed": False, "message": "sesión no encontrada"}

            if row["status"] not in ("pending", "running"):
                return {
                    "agent_id": agent_id,
                    "killed": False,
                    "message": f"agente ya terminado (status={row['status']})",
                }

            pid = row["pid"]
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # proceso ya muerto
        else:
            proc.terminate()

        # Grace period
        try:
            if proc is not None:
                proc.wait(timeout=_KILL_GRACE_SECONDS)
            else:
                import time

                time.sleep(min(_KILL_GRACE_SECONDS, 2))
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
            elif pid := locals().get("pid"):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # Cleanup
        if agent_id in self._processes:
            del self._processes[agent_id]

        _db_update_status(agent_id, "killed")
        _db_append_event(agent_id, "process_ended", {"reason": "killed_by_orchestrator"})
        self._cleanup_config(agent_id)

        log.info("agent %s killed", agent_id)
        return {"agent_id": agent_id, "killed": True, "message": "terminado correctamente"}

    def get_status(self, agent_id: str) -> dict:
        """Devuelve el estado en tiempo real de un sub-agente.

        Combina el estado en DB con el estado real del proceso (poll).

        Returns:
            {agent_id, role, goal, status, pid, elapsed_seconds, process_alive}
        """
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, role, goal, status, pid,
                              started_at,
                              EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed_seconds
                       FROM agent_sessions WHERE id = %s""",
                    (agent_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return {"agent_id": agent_id, "error": "sesión no encontrada"}

        result = dict(row)
        result["agent_id"] = result.pop("id")

        # Verificar si el proceso sigue vivo
        proc = self._processes.get(agent_id)
        if proc is not None:
            returncode = proc.poll()
            process_alive = returncode is None
            if not process_alive and result["status"] == "running":
                # Proceso murió, actualizar DB
                status = "completed" if returncode == 0 else "failed"
                _db_update_status(agent_id, status, result_summary=f"exit_code={returncode}")
                _db_append_event(agent_id, "process_ended", {"exit_code": returncode})
                result["status"] = status
                del self._processes[agent_id]
                self._cleanup_config(agent_id)
        else:
            process_alive = False

        result["process_alive"] = process_alive
        if result.get("started_at"):
            result["started_at"] = result["started_at"].isoformat()

        return result

    @staticmethod
    def _cleanup_config(agent_id: str) -> None:
        """Borra el config temporal del agente si existe."""
        config_path = _AGENT_CONFIG_DIR / f"agent-{agent_id}.yaml"
        try:
            config_path.unlink(missing_ok=True)
        except OSError:
            pass

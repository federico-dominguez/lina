<<<<<<< HEAD
import json
import logging
import os
import tempfile
import threading
import time
import uuid
=======
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

import logging
import os
import signal
import subprocess
import uuid
from pathlib import Path
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)
from typing import Any

import psycopg2
import psycopg2.extras
<<<<<<< HEAD
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("lina-orchestrator.spawner")

_GOOSED_URL = os.environ.get("LINA_GOOSED_URL", "https://goosed:3000").rstrip("/")
_GOOSED_SECRET = os.environ.get("LINA_GOOSED_SECRET", "")
_DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@lina-db:5432/lina")
_HTTP_TIMEOUT = int(os.environ.get("LINA_SPAWN_HTTP_TIMEOUT", "30"))
_RESUME_TIMEOUT = int(os.environ.get("LINA_RESUME_TIMEOUT", "180"))
_POST_RESUME_DELAY = int(os.environ.get("LINA_POST_RESUME_DELAY", "5"))


def _goosed_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if _GOOSED_SECRET:
        headers["x-secret-key"] = _GOOSED_SECRET
    return headers
=======
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
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)


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
<<<<<<< HEAD
=======
    import json

>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)
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


<<<<<<< HEAD
def _build_system_prompt(role: str, goal: str, session_id: str) -> str:
    return (
        f"Sos un sub-agente de LINA con rol '{role}'.\n"
        f"Tu session_id de agente es: {session_id}\n\n"
        f"OBJETIVO DE ESTA SESIÓN:\n{goal}\n\n"
        "INSTRUCCIONES OPERACIONALES:\n"
        "- Al completar el objetivo, llamá update_agent_status() del MCP lina-db "
        "con status='completed' y result_summary describiendo qué hiciste.\n"
        "- Llamá append_agent_event() para loguear hitos importantes.\n"
        "- Al INICIO de cada turno, llamá get_pending_instructions() del MCP lina-db "
        f"con session_id='{session_id}' para recibir instrucciones adicionales de LINA.\n"
        "- Si encontrás instrucciones pendientes, ejecutalas antes de continuar.\n"
        "- No pedís confirmación humana para acciones normales.\n"
        "- Si encontrás un bloqueante insalvable, llamá update_agent_status() con "
        "status='failed' y describí el problema en result_summary."
    )


def _resume_and_stream(
    agent_id: str,
    goosed_session_id: str,
    goal: str,
    role: str,
) -> None:
    """Runs entirely in a daemon thread: loads extensions then drives SSE stream.

    Separated from spawn() so the MCP tool call returns immediately (before the
    long /agent/resume, which can take 60-180s for 20+ MCPs to connect).
    """
    headers = _goosed_headers()

    # ── 1. Resume / load extensions ──────────────────────────────────────────
    resume_ok = False
    try:
        r_resume = requests.post(
            f"{_GOOSED_URL}/agent/resume",
            json={"session_id": goosed_session_id, "load_model_and_extensions": True},
            headers=headers,
            verify=False,  # noqa: S501
            timeout=_RESUME_TIMEOUT,
        )
        r_resume.raise_for_status()
        resume_ok = True
        log.info("sub-agent %s: resume OK", agent_id)
    except requests.Timeout:
        log.warning(
            "sub-agent %s: resume timed out after %ds — proceeding",
            agent_id,
            _RESUME_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.warning("sub-agent %s: resume warning: %s", agent_id, exc)

    if not resume_ok:
        log.info("sub-agent %s: waiting %ds for extensions to settle", agent_id, _POST_RESUME_DELAY)
        time.sleep(_POST_RESUME_DELAY)

    # ── 2. Update DB to running ───────────────────────────────────────────────
    _db_update_status(agent_id, "running", config_path=f"goosed:{goosed_session_id}")
    _db_append_event(agent_id, "process_started", {"goosed_session_id": goosed_session_id})

    # ── 3. Drive SSE stream ───────────────────────────────────────────────────
    system_prompt = _build_system_prompt(role, goal, agent_id)
    full_message = f"{system_prompt}\n\n---\nOBJETIVO:\n{goal}"
    payload = {
        "session_id": goosed_session_id,
        "user_message": {
            "role": "user",
            "created": int(time.time()),
            "content": [{"type": "text", "text": full_message}],
            "metadata": {"userVisible": False, "agentVisible": True},
        },
    }
    log.info("sub-agent %s: starting SSE stream (goosed_session=%s)", agent_id, goosed_session_id)
    try:
        with requests.post(
            f"{_GOOSED_URL}/reply",
            json=payload,
            headers=headers,
            stream=True,
            verify=False,  # noqa: S501
            timeout=(10, 600),
        ) as resp:
            if not resp.ok:
                log.error(
                    "sub-agent %s: /reply %d: %s",
                    agent_id,
                    resp.status_code,
                    resp.text[:200],
                )
                _db_update_status(agent_id, "failed", result_summary=f"goosed_{resp.status_code}")
                return

            finish_reason: str | None = None
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith("data: "):
                    try:
                        data = json.loads(raw_line[6:])
                    except json.JSONDecodeError:
                        continue
                    etype = (data.get("type") or data.get("event_type", "")).lower()
                    if etype == "finish":
                        finish_reason = data.get("reason", "end_turn")
                        break
                    if etype in ("message_stop", "done", "complete", "error"):
                        finish_reason = etype
                        break

        # ── 4. Update final status ────────────────────────────────────────────
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM agent_sessions WHERE id = %s", (agent_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        current_status = row[0] if row else "unknown"
        log.info("sub-agent %s: done (finish=%s db=%s)", agent_id, finish_reason, current_status)
        if current_status == "running":
            terminal = (
                "failed"
                if (
                    finish_reason is None
                    or str(finish_reason).lower() in ("max_tokens", "maxtokens", "error")
                )
                else "completed"
            )
            _db_update_status(agent_id, terminal, result_summary=f"stream_ended_{finish_reason}")
            _db_append_event(agent_id, "process_ended", {"finish_reason": finish_reason})

    except requests.RequestException as exc:
        log.error("sub-agent %s: request error: %s", agent_id, exc)
        _db_update_status(agent_id, "failed", result_summary=f"request_error: {exc}")


class SpawnerService:
    """Gestiona el ciclo de vida de sub-agentes via HTTP API de goosed."""

    def __init__(self, policy_service: Any) -> None:
        self._policy = policy_service
        self._goosed_sessions: dict[str, str] = {}

    def spawn(self, role: str, goal: str) -> dict:
=======
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
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)
        known = self._policy.list_roles()
        if role not in known:
            raise ValueError(f"Rol desconocido: {role!r}. Roles disponibles: {known}")

<<<<<<< HEAD
        agent_id = uuid.uuid4().hex
        _db_create_session(agent_id, role, goal)
        _db_append_event(agent_id, "spawn_requested", {"role": role, "goal": goal[:200]})

        headers = _goosed_headers()

        try:
            r_start = requests.post(
                f"{_GOOSED_URL}/agent/start",
                json={"working_dir": tempfile.gettempdir()},
                headers=headers,
                verify=False,  # noqa: S501
                timeout=_HTTP_TIMEOUT,
            )
            r_start.raise_for_status()
        except requests.RequestException as exc:
            _db_update_status(agent_id, "failed", result_summary=f"agent_start_failed: {exc}")
            raise RuntimeError(f"No se pudo crear sesión en goosed: {exc}") from exc

        data = r_start.json()
        goosed_session_id = data.get("id") or data.get("session_id")
        if not goosed_session_id:
            _db_update_status(agent_id, "failed", result_summary="no_session_id")
            raise RuntimeError(f"goosed no devolvió session_id: {data}")

        log.info("sub-agent %s: goosed session = %s", agent_id, goosed_session_id)

        self._goosed_sessions[agent_id] = goosed_session_id
        t = threading.Thread(
            target=_resume_and_stream,
            args=(agent_id, goosed_session_id, goal, role),
            daemon=True,
            name=f"agent-{agent_id[:8]}",
        )
        t.start()

        log.info("sub-agent %s: background thread started (%s)", agent_id, t.name)

        return {
            "agent_id": agent_id,
            "role": role,
            "goal": goal,
            "goosed_session_id": goosed_session_id,
            "status": "pending",
            "message": "Sub-agente lanzado. Resume y stream corren en background.",
        }

    def kill(self, agent_id: str) -> dict:
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT status FROM agent_sessions WHERE id = %s", (agent_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return {"agent_id": agent_id, "killed": False, "message": "sesión no encontrada"}
        if row["status"] not in ("pending", "running"):
            msg = f"ya terminado ({row['status']})"
            return {"agent_id": agent_id, "killed": False, "message": msg}

        _db_update_status(agent_id, "killed")
        _db_append_event(agent_id, "kill_requested", {"by": "orchestrator"})
        return {"agent_id": agent_id, "killed": True, "message": "kill registrado en DB"}

    def get_status(self, agent_id: str) -> dict:
=======
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
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
<<<<<<< HEAD
                    """SELECT id AS agent_id, role, goal, status, pid,
                              EXTRACT(EPOCH FROM (NOW() - created_at))::INT AS elapsed_seconds
=======
                    """SELECT id, role, goal, status, pid,
                              started_at,
                              EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed_seconds
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)
                       FROM agent_sessions WHERE id = %s""",
                    (agent_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
<<<<<<< HEAD
            return {"error": f"agente {agent_id!r} no encontrado"}

        result = dict(row)
        result["process_alive"] = False
        return result
=======
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
>>>>>>> d022f79 (feat(#83,#84,#85): agent control plane — schema, spawner, orchestrator tools)

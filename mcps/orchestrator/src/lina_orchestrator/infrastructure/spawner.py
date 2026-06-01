import json
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Any

import psycopg2
import psycopg2.extras
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
        ", ended_at = NOW()"
        if status in {"completed", "failed", "killed", "timeout"}
        else ""
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
        known = self._policy.list_roles()
        if role not in known:
            raise ValueError(f"Rol desconocido: {role!r}. Roles disponibles: {known}")

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
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id AS agent_id, role, goal, status, pid,
                              EXTRACT(EPOCH FROM (NOW() - created_at))::INT AS elapsed_seconds
                       FROM agent_sessions WHERE id = %s""",
                    (agent_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return {"error": f"agente {agent_id!r} no encontrado"}

        result = dict(row)
        result["process_alive"] = False
        return result

"""lifecycle.py — Watchdog de sub-agentes: timeouts, retry, dead-letter (issue #89).

Arranca un thread daemon que cada _WATCHDOG_INTERVAL segundos:
  1. Enforce timeouts → kill agent si excedió max_runtime_minutes
  2. Detect crashes → programa retry con backoff exponencial
  3. Retry backoff → lanza restart si retry_at ya pasó
  4. Dead-letter → mueve a DLQ si retry_count >= _MAX_RETRIES

Backoff: 1m, 5m, 15m (cap). Patrón Kubernetes CrashLoopBackOff.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg2
import psycopg2.extras

if TYPE_CHECKING:
    from lina_orchestrator.application.policy_service import PolicyService

log = logging.getLogger("lina-orchestrator.lifecycle")

_WATCHDOG_INTERVAL = 30  # segundos entre chequeos
_BACKOFF_INTERVALS = [60, 300, 900]  # 1m, 5m, 15m
_MAX_RETRIES = 3

_GOOSED_BIN = os.environ.get(
    "LINA_GOOSED_BIN",
    str(Path("/home/user/lina/vendor/goosed-linux-amd64")),
)
_DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina@localhost:5432/lina",
)


def _db_conn():
    """Conexión directa a PostgreSQL."""
    return psycopg2.connect(_DB_URL)


class LifecycleService:
    """Watchdog que monitorea sub-agentes y maneja fallos.

    Uso:
        lifecycle = LifecycleService(policy_service)
        lifecycle.start()  # arranca thread daemon
        ...
        lifecycle.stop()   # opcional, al cerrar el orquestador
    """

    def __init__(self, policy_service: PolicyService) -> None:
        self._policy = policy_service
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running_retries: dict[str, float] = {}  # session_id → started_at

    # ─── Thread lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Arranca el watchdog en un thread daemon."""
        if self._thread is not None and self._thread.is_alive():
            log.warning("lifecycle watchdog ya está corriendo")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="lifecycle-watchdog",
            daemon=True,
        )
        self._thread.start()
        log.info("lifecycle watchdog started (interval=%ds)", _WATCHDOG_INTERVAL)

    def stop(self) -> None:
        """Detiene el watchdog."""
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ─── Loop principal ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Loop infinito que ejecuta el watchdog."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("lifecycle watchdog error en tick")
            self._stop_event.wait(_WATCHDOG_INTERVAL)

    def _tick(self) -> None:
        """Un ciclo del watchdog: timeout, retry, DLQ."""
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 1. Enforce timeouts
                self._enforce_timeouts(cur, conn)

                # 2. Retry backoff (agentes failed con retry_at <= NOW)
                self._process_retries(cur, conn)

            conn.commit()
        finally:
            conn.close()

    # ─── 1. Timeout enforcement ────────────────────────────────────────────────

    def _enforce_timeouts(
        self,
        cur: psycopg2.extras.RealDictCursor,
        conn: psycopg2.extensions.connection,
    ) -> None:
        """Busca agentes running que excedieron max_runtime_minutes y los mata.

        max_runtime_minutes se obtiene de policies.yaml por rol.
        """
        cur.execute(
            """SELECT id, role, goal, pid, started_at
               FROM agent_sessions
               WHERE status = 'running'
                 AND started_at IS NOT NULL
               LIMIT 50"""
        )
        running = cur.fetchall()

        for row in running:
            session_id = row["id"]
            role = row["role"]
            started_at = row["started_at"]
            pid = row["pid"]

            # Obtener max_runtime del policy
            try:
                role_policy = self._policy.get_role(role)
                max_minutes = role_policy.get("max_runtime_minutes", 120)
            except KeyError:
                max_minutes = 120  # fallback conservador

            if started_at is None or pid is None:
                continue

            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            max_seconds = max_minutes * 60

            if elapsed > max_seconds:
                log.warning(
                    "TIMEOUT: agent=%s role=%s pid=%d elapsed=%.0fs max=%ds",
                    session_id, role, pid, elapsed, max_seconds,
                )
                # Matar el proceso
                self._kill_process_group(pid)

                # Actualizar DB
                cur.execute(
                    """UPDATE agent_sessions
                       SET status = 'timeout',
                           ended_at = NOW(),
                           watchdog_note = %s,
                           retry_count = retry_count + 1
                       WHERE id = %s""",
                    (f"timeout: excedió {max_minutes}m (elapsed={int(elapsed)}s)", session_id),
                )
                _db_append_event_static(session_id, "timeout", {
                    "max_runtime_minutes": max_minutes,
                    "elapsed_seconds": int(elapsed),
                    "pid": pid,
                })

    # ─── 2. Retry con backoff ──────────────────────────────────────────────────

    def _process_retries(
        self,
        cur: psycopg2.extras.RealDictCursor,
        conn: psycopg2.extensions.connection,
    ) -> None:
        """Revisa agentes en failed/timeout con retry_at <= NOW y los reintenta.

        Si retry_count >= _MAX_RETRIES → mueve a dead_letter.
        """
        cur.execute(
            """SELECT id, role, goal, status, retry_count, last_error, watchdog_note
               FROM agent_sessions
               WHERE status IN ('failed', 'timeout')
                 AND retry_count < %s
                 AND (retry_at IS NULL OR retry_at <= NOW())
               ORDER BY retry_count ASC
               LIMIT 10""",
            (_MAX_RETRIES,),
        )
        candidates = cur.fetchall()

        for row in candidates:
            session_id = row["id"]
            role = row["role"]
            goal = row["goal"]
            retry_count = row["retry_count"]
            last_error = row["last_error"] or row.get("watchdog_note") or "unknown"

            # Si ya excedió max retries → dead letter
            if retry_count >= _MAX_RETRIES:
                self._move_to_dead_letter(cur, session_id, role, goal, retry_count, last_error)
                continue

            # Calcular backoff
            backoff = _BACKOFF_INTERVALS[min(retry_count, len(_BACKOFF_INTERVALS) - 1)]
            retry_at = datetime.now(timezone.utc).timestamp() + backoff

            # Lanzar nuevo goosed (reintento)
            try:
                new_session_id = self._restart_agent(role, goal, retry_count + 1)
                log.info(
                    "RETRY: agent=%s role=%s retry=%d/%d → new_session=%s backoff=%ds",
                    session_id, role, retry_count + 1, _MAX_RETRIES, new_session_id, backoff,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "RETRY FAILED: agent=%s role=%s error=%s",
                    session_id, role, exc,
                )
                cur.execute(
                    "UPDATE agent_sessions SET last_error = %s WHERE id = %s",
                    (f"retry_failed: {exc}", session_id),
                )

    def _restart_agent(self, role: str, goal: str, retry_count: int) -> str:
        """Lanza un nuevo sub-agente goosed (reintento).

        Returns:
            Nuevo session_id.
        """
        import uuid

        from lina_orchestrator.infrastructure.spawner import (  # noqa: PLC0415
            _db_append_event,
            _db_create_session,
            _db_update_status,
            _generate_agent_config,
        )

        session_id = uuid.uuid4().hex

        _db_create_session(session_id, role, goal)
        _db_append_event(session_id, "spawn_requested", {
            "role": role,
            "goal": goal[:200],
            "retry": True,
            "retry_count": retry_count,
        })

        # Obtener MCPs permitidos para el rol
        role_policy = self._policy.get_role(role)
        allowed_mcps = role_policy.get("allowed_mcps", [])

        config_path = _generate_agent_config(session_id, role, allowed_mcps)

        env = os.environ.copy()
        env["LINA_AGENT_SESSION_ID"] = session_id
        env["LINA_AGENT_ROLE"] = role

        cmd = [_GOOSED_BIN, "run", "--config", str(config_path)]

        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            preexec_fn=os.setsid,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        _db_update_status(
            session_id,
            "running",
            pid=proc.pid,
            config_path=str(config_path),
        )
        _db_append_event(session_id, "process_started", {"pid": proc.pid, "retry": True, "retry_count": retry_count})

        return session_id

    # ─── 3. Dead-letter ────────────────────────────────────────────────────────

    def _move_to_dead_letter(
        self,
        cur: psycopg2.extras.RealDictCursor,
        session_id: str,
        role: str,
        goal: str | None,
        retry_count: int,
        last_error: str,
    ) -> None:
        """Mueve un agente a la dead-letter queue."""
        log.warning(
            "DEAD LETTER: agent=%s role=%s retries=%d error=%s",
            session_id, role, retry_count, last_error,
        )
        cur.execute(
            """INSERT INTO dead_letter (session_id, role, goal, retry_count, last_error)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (session_id, role, goal, retry_count, last_error),
        )
        cur.execute(
            "UPDATE agent_sessions SET last_error = %s, retry_count = %s WHERE id = %s",
            (f"dead_letter: {last_error}", retry_count, session_id),
        )
        _db_append_event_static(session_id, "dead_letter", {
            "retry_count": retry_count,
            "last_error": last_error,
        })

    # ─── 4. Kill con propagación (process group) ───────────────────────────────

    @staticmethod
    def kill_process_group(pid: int) -> None:
        """Envía SIGTERM a todo el grupo de procesos del agente.

        Usa os.killpg para propagar la señal a todos los hijos.
        Si el proceso no existe, captura ProcessLookupError.
        """
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # proceso ya terminó
        except PermissionError:
            log.warning("killpg: permiso denegado para pid=%d", pid)

    @staticmethod
    def _kill_process_group(pid: int) -> None:
        """Envía SIGTERM + SIGKILL (grace period) a todo el process group."""
        try:
            os.killpg(pid, signal.SIGTERM)
            time.sleep(2)  # grace period
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            log.warning("killpg: permiso denegado para pid=%d", pid)

    # ─── List dead letter ──────────────────────────────────────────────────────

    def list_dead_letter(self, limit: int = 20) -> list[dict]:
        """Lista los agentes en la dead-letter queue.

        Args:
            limit: máximo de resultados.

        Returns:
            Lista de dicts con session_id, role, goal, retry_count, last_error, moved_at.
        """
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT session_id, role, goal, retry_count, last_error, moved_at
                       FROM dead_letter
                       ORDER BY moved_at DESC
                       LIMIT %s""",
                    (limit,),
                )
                rows = []
                for row in cur.fetchall():
                    d = dict(row)
                    if d.get("moved_at"):
                        d["moved_at"] = d["moved_at"].isoformat()
                    rows.append(d)
                return rows
        finally:
            conn.close()

    # ─── Resumen de agentes fallidos ──────────────────────────────────────────

    def get_failed_summary(self) -> list[dict]:
        """Resumen de agentes en estado failed/timeout/killed.

        Returns:
            Lista de dicts desde la vista failed_agents.
        """
        conn = _db_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM failed_agents LIMIT 20")
                rows = [dict(r) for r in cur.fetchall()]
                for d in rows:
                    for key in ("started_at", "ended_at", "dead_letter_at"):
                        if d.get(key):
                            d[key] = d[key].isoformat()
                return rows
        finally:
            conn.close()


# ─── Helper estático (sin instancia) ────────────────────────────────────────────


def _db_append_event_static(session_id: str, kind: str, payload: dict) -> None:
    """Append de evento sin depender de instancia Spawner."""
    import json as _json

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_events (session_id, kind, payload_json) VALUES (%s, %s, %s)",
                (session_id, kind, _json.dumps(payload)),
            )
        conn.commit()
    finally:
        conn.close()

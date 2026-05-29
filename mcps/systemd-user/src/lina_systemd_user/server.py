"""LINA MCP systemd-user — control de servicios systemd --user.

Restricciones:
    - Solo unidades cuyo nombre matchee `LINA_SYSTEMD_UNIT_REGEX`
      (default: `^lina-`) pueden ser start/stop/restart-eadas.
    - status/logs son read-only y no tienen esa restricción (puede inspeccionar
      cualquier unidad --user del propio usuario).
    - Nunca toca el bus de sistema (--system).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-systemd-user] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-systemd-user")

WRITABLE_REGEX = re.compile(os.environ.get("LINA_SYSTEMD_UNIT_REGEX", r"^lina-"))
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@_.\-]+(\.(service|socket|timer|path|target))?$")

mcp = FastMCP("lina-systemd-user")


def _validate_unit(unit: str) -> str:
    unit = unit.strip()
    if not UNIT_NAME_RE.match(unit):
        raise ValueError(f"nombre de unidad inválido: {unit!r}")
    if "." not in unit:
        unit += ".service"
    return unit


def _require_writable(unit: str) -> None:
    if not WRITABLE_REGEX.search(unit):
        raise PermissionError(
            f"unidad {unit!r} no está en el namespace permitido para escritura "
            f"({WRITABLE_REGEX.pattern})"
        )


def _systemctl(*args: str, timeout: int = 15) -> dict:
    cmd = ["systemctl", "--user", *args]
    log.info("exec: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-8_000:],
            "stderr": proc.stderr[-4_000:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": f"timeout {timeout}s"}


# ─── read-only ────────────────────────────────────────────────────────────────


@mcp.tool()
def svc_status(unit: str) -> dict:
    """Estado completo de una unidad (equivalente a `systemctl --user status`)."""
    u = _validate_unit(unit)
    r = _systemctl("status", u, "--no-pager", "-l")
    return {"unit": u, **r}


@mcp.tool()
def svc_is_active(unit: str) -> str:
    """Devuelve 'active' | 'inactive' | 'failed' | 'activating' | ..."""
    u = _validate_unit(unit)
    r = _systemctl("is-active", u)
    return (r["stdout"] or r["stderr"]).strip() or "unknown"


@mcp.tool()
def svc_is_enabled(unit: str) -> str:
    """Devuelve 'enabled' | 'disabled' | 'static' | ..."""
    u = _validate_unit(unit)
    r = _systemctl("is-enabled", u)
    return (r["stdout"] or r["stderr"]).strip() or "unknown"


@mcp.tool()
def svc_list_lina() -> list[dict]:
    """Lista todas las unidades --user cuyo nombre matchea el regex permitido."""
    r = _systemctl("list-units", "--type=service", "--all", "--no-legend", "--plain")
    out = []
    for line in r["stdout"].splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        desc = parts[4] if len(parts) > 4 else ""
        if WRITABLE_REGEX.search(unit):
            out.append(
                {"unit": unit, "load": load, "active": active, "sub": sub, "description": desc}
            )
    return out


@mcp.tool()
def svc_logs(unit: str, lines: int = 100) -> str:
    """Últimas N líneas del journal de la unidad (max 1000)."""
    u = _validate_unit(unit)
    n = max(1, min(int(lines), 1000))
    cmd = ["journalctl", "--user", "-u", u, "-n", str(n), "--no-pager"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        return proc.stdout[-32_000:] or proc.stderr[-4_000:]
    except subprocess.TimeoutExpired:
        return "TIMEOUT consultando journalctl"


# ─── write — limitado a unidades LINA ─────────────────────────────────────────


@mcp.tool()
def svc_start(unit: str) -> dict:
    """Inicia una unidad LINA."""
    u = _validate_unit(unit)
    _require_writable(u)
    return {"unit": u, **_systemctl("start", u)}


@mcp.tool()
def svc_stop(unit: str) -> dict:
    """Detiene una unidad LINA."""
    u = _validate_unit(unit)
    _require_writable(u)
    return {"unit": u, **_systemctl("stop", u)}


@mcp.tool()
def svc_restart(unit: str) -> dict:
    """Reinicia una unidad LINA."""
    u = _validate_unit(unit)
    _require_writable(u)
    return {"unit": u, **_systemctl("restart", u)}


@mcp.tool()
def svc_reload(unit: str) -> dict:
    """Reload de una unidad LINA (si la soporta; si no, restart)."""
    u = _validate_unit(unit)
    _require_writable(u)
    return {"unit": u, **_systemctl("reload-or-restart", u)}


@mcp.tool()
def svc_daemon_reload() -> dict:
    """Recarga la definición de units (tras editar archivos .service)."""
    return _systemctl("daemon-reload")


def main() -> None:
    log.info("starting (writable_regex=%s)", WRITABLE_REGEX.pattern)
    mcp.run()


if __name__ == "__main__":
    main()

"""LINA MCP shell-policy — ejecuta comandos con policy de seguridad.

Política:
    - Se rechazan patrones destructivos catastróficos (rm -rf /, dd a /dev/sd*,
      :(){:|:&};:, mkfs, etc.) SIEMPRE, independientemente de los flags.
    - Comandos con `sudo` / `su` / `pkexec` / `doas` requieren
      `LINA_SHELL_ALLOW_SUDO=1` Y `allow_sudo=True` en la llamada.
    - Comandos que modifican el sistema (`apt`, `dnf`, `systemctl` sin --user)
      caen en la misma categoría que sudo.
    - Timeout por defecto = `LINA_SHELL_TIMEOUT_SEC` (60s).
    - Audit log en ~/lina/logs/shell-policy.audit.log

Tools:
    - sh_run(command, cwd=None, timeout=None, allow_sudo=False)
    - sh_explain(command) → devuelve qué haría la policy sin ejecutar.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-shell-policy] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-shell-policy")

ALLOW_SUDO_ENV = os.environ.get("LINA_SHELL_ALLOW_SUDO", "0") == "1"
DEFAULT_TIMEOUT = int(os.environ.get("LINA_SHELL_TIMEOUT_SEC", "60"))
MAX_TIMEOUT = 600

_AUDIT_DIR = Path.home() / "lina" / "logs"
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
_AUDIT_LOG = _AUDIT_DIR / "shell-policy.audit.log"

mcp = FastMCP("lina-shell-policy")


# ─── policy ───────────────────────────────────────────────────────────────────

# Patrones que NUNCA se ejecutan, ni con sudo habilitado.
HARD_DENY: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b.*\s/(\s|$|\*)"),
        "rm -rf sobre raíz",
    ),
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b.*\/\.\.(/|$)"),
        "rm -rf con path traversal",
    ),
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(--no-preserve-root|/\*)"
        ),
        "rm -rf --no-preserve-root",
    ),
    (re.compile(r"\bmkfs(\.|\s)"), "mkfs (formateo)"),
    (re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|hd|mmcblk)"), "dd hacia dispositivo de bloques"),
    (re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;\s*:"), "fork bomb"),
    (
        re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b"),
        "apagado/reinicio del sistema",
    ),
    (re.compile(r"\bchmod\s+(-R\s+)?0?777\s+/"), "chmod 777 sobre raíz"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(bash|sh|zsh)\b"), "curl|bash pipe a shell"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|mmcblk)"), "redirección a dispositivo de bloques"),
]

# Patrones que requieren ALLOW_SUDO_ENV + allow_sudo=True.
PRIVILEGED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*(sudo|doas|pkexec)\b"), "sudo/doas/pkexec"),
    (re.compile(r"^\s*su\b"), "su"),
    (re.compile(r"\bapt(-get)?\s+(install|remove|purge|upgrade|dist-upgrade)\b"), "apt write"),
    (re.compile(r"\b(dnf|yum)\s+(install|remove|upgrade)\b"), "dnf/yum write"),
    (re.compile(r"\bpacman\s+-(S|R|U|Syu)\b"), "pacman write"),
    (
        re.compile(r"\bsystemctl\b(?!\s+--user)\s+(start|stop|restart|enable|disable|mask)"),
        "systemctl system (sin --user)",
    ),
]


@dataclass
class PolicyDecision:
    allowed: bool
    category: str  # 'safe' | 'privileged' | 'denied'
    reason: str


def evaluate(command: str, allow_sudo: bool) -> PolicyDecision:
    cmd = command.strip()
    if not cmd:
        return PolicyDecision(False, "denied", "comando vacío")
    for pat, label in HARD_DENY:
        if pat.search(cmd):
            return PolicyDecision(False, "denied", f"hard-deny: {label}")
    for pat, label in PRIVILEGED:
        if pat.search(cmd):
            if allow_sudo and ALLOW_SUDO_ENV:
                return PolicyDecision(True, "privileged", f"privileged-allowed: {label}")
            return PolicyDecision(
                False,
                "denied",
                f"requiere privilegios ({label}); allow_sudo={allow_sudo}, "
                f"LINA_SHELL_ALLOW_SUDO={int(ALLOW_SUDO_ENV)}",
            )
    return PolicyDecision(True, "safe", "ok")


def _audit(command: str, decision: PolicyDecision, **extra: object) -> None:
    record = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "command": command,
        "category": decision.category,
        "allowed": decision.allowed,
        "reason": decision.reason,
        **extra,
    }
    line = json.dumps(record, ensure_ascii=False)
    log.info(line)
    try:
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as e:
        log.warning("audit log fail: %s", e)


# ─── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def sh_explain(command: str, allow_sudo: bool = False) -> dict:
    """Devuelve la decisión de policy SIN ejecutar el comando."""
    d = evaluate(command, allow_sudo)
    return {"allowed": d.allowed, "category": d.category, "reason": d.reason}


@mcp.tool()
def sh_run(
    command: str,
    cwd: str | None = None,
    timeout: int | None = None,
    allow_sudo: bool = False,
) -> dict:
    """Ejecuta un comando shell sujeto a policy.

    Devuelve un dict con: exit_code, stdout, stderr, duration_ms, decision.
    Si la policy rechaza, devuelve exit_code=-1 y NO ejecuta nada.
    """
    decision = evaluate(command, allow_sudo)
    if not decision.allowed:
        _audit(command, decision, executed=False)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"POLICY DENY: {decision.reason}",
            "duration_ms": 0,
            "decision": {"category": decision.category, "reason": decision.reason},
        }

    eff_timeout = min(int(timeout) if timeout else DEFAULT_TIMEOUT, MAX_TIMEOUT)
    work_dir = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.home())
    t0 = _dt.datetime.now()
    try:
        proc = subprocess.run(  # noqa: S602 — shell=True es intencional y policy-gated
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=eff_timeout,
            executable="/bin/bash",
        )
        result = {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-32_000:],
            "stderr": proc.stderr[-8_000:],
        }
    except subprocess.TimeoutExpired as e:
        result = {
            "exit_code": 124,
            "stdout": (e.stdout or b"").decode("utf-8", "replace")[-32_000:] if e.stdout else "",
            "stderr": f"TIMEOUT tras {eff_timeout}s",
        }
    duration_ms = int((_dt.datetime.now() - t0).total_seconds() * 1000)
    result["duration_ms"] = duration_ms
    result["decision"] = {"category": decision.category, "reason": decision.reason}
    _audit(command, decision, executed=True, exit_code=result["exit_code"], duration_ms=duration_ms)
    return result


@mcp.tool()
def sh_which(binary: str) -> str | None:
    """Resuelve un binario en PATH (utilidad común, sin policy)."""
    from shutil import which

    if not re.match(r"^[A-Za-z0-9._/-]+$", binary):
        raise ValueError("nombre de binario inválido")
    return which(binary)


@mcp.tool()
def sh_quote(args: list[str]) -> str:
    """Devuelve un string shell-safe a partir de una lista de argumentos."""
    return shlex.join(args)


def main() -> None:
    log.info("starting (allow_sudo_env=%s, default_timeout=%ds)", ALLOW_SUDO_ENV, DEFAULT_TIMEOUT)
    mcp.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""LINA Self-Healthcheck — verifica, repara y reporta el estado de LINA.

Uso:
    python3 bin/lina-self-health.py [--fix] [--notify]

Flags:
    --fix      Intenta reparar servicios fallados automáticamente
    --notify   Envía reporte al grupo Comm via send-bot.py

Design:
    Cada check es un bloque independiente. Si falla → se intenta fix (si --fix).
    Al final, un summary JSON se guarda en /tmp/lina-health-last.json
    y (si --notify) se envía al grupo Comm.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

_HOME = Path.home()
_LINA = _HOME / "lina"
_RESULT_FILE = Path("/tmp/lina-health-last.json")

_CRITICAL_SERVICES = (
    "lina-goosed.service",
    "lina-gateway-host.service",
    "lina-comm-bridge.service",
    "lina-cline-daemon.service",
    "lina-pipeline-listener.service",
    "lina-earlyoom.service",
)

_OPTIONAL_SERVICES = (
    "lina-goose-restart.service",
)

_DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

_SEND_BOT = _LINA / "comm" / "send-bot.py"

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ok(msg: str) -> str:
    return f"✅ {msg}"


def _warn(msg: str) -> str:
    return f"⚠️ {msg}"


def _fail(msg: str) -> str:
    return f"❌ {msg}"


def _run(cmd: list[str], timeout: float = 15.0) -> dict:
    """Run a command, return {ok, stdout, stderr}."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"command not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def _systemctl(*args: str, timeout: float = 15.0) -> dict:
    """Call systemctl --user with args."""
    return _run(["systemctl", "--user", *args], timeout=timeout)


# ─── Checks ──────────────────────────────────────────────────────────────────


class HealthReport:
    """Collects check results and generates a summary."""

    def __init__(self) -> None:
        self.ts = datetime.now(UTC)
        self.checks: list[dict] = []
        self.fixes: list[str] = []
        self._all_ok = True

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            self._all_ok = False

    def note_fix(self, msg: str) -> None:
        self.fixes.append(msg)

    @property
    def all_ok(self) -> bool:
        return self._all_ok

    @property
    def summary(self) -> str:
        ok_count = sum(1 for c in self.checks if c["ok"])
        total = len(self.checks)
        status = "🟢 ALL OK" if self.all_ok else "🔴 ISSUES DETECTED"
        lines = [
            f"🩺 LINA Self-Healthcheck — {self.ts.strftime('%H:%M:%S')}",
            f"   Status: {status}  ({ok_count}/{total} checks pasaron)",
            "",
        ]
        for c in self.checks:
            icon = "✅" if c["ok"] else "❌"
            lines.append(f"   {icon} {c['name']}: {c['detail']}")
        if self.fixes:
            lines.append("")
            lines.append(f"   🔧 Fixes aplicados ({len(self.fixes)}):")
            for f in self.fixes:
                lines.append(f"      • {f}")
        return "\n".join(lines)

    @property
    def compact_summary(self) -> str:
        """One-line summary for Telegram notifications."""
        ok_count = sum(1 for c in self.checks if c["ok"])
        total = len(self.checks)
        status = "🟢" if self.all_ok else "🔴"
        return f"{status} LINA Health: {ok_count}/{total} | fixes={len(self.fixes)}"

    def save(self) -> None:
        _RESULT_FILE.write_text(
            json.dumps(
                {
                    "ts": self.ts.isoformat(),
                    "all_ok": self.all_ok,
                    "checks": self.checks,
                    "fixes": self.fixes,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    def to_telegram(self) -> str:
        """Full report formatted for plain text (send-bot no acepta HTML)."""
        ok_count = sum(1 for c in self.checks if c["ok"])
        total = len(self.checks)
        lines = [
            f"🩺 LINA Self-Healthcheck",
            f"{self.ts.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Estado: {'🟢 OK' if self.all_ok else '🔴 ISSUES'}  ({ok_count}/{total})",
            "",
        ]
        for c in self.checks:
            icon = "✅" if c["ok"] else "❌"
            lines.append(f"{icon} {c['name']}: {c['detail']}")
        if self.fixes:
            lines.append("")
            lines.append(f"🔧 Fixes ({len(self.fixes)}):")
            for f in self.fixes:
                lines.append(f"  • {f}")
        return "\n".join(lines)


def check_systemd_services(report: HealthReport, fix: bool = False) -> None:
    """Check critical and optional systemd services."""
    for svc in _CRITICAL_SERVICES:
        r = _systemctl("is-active", svc)
        active = r["stdout"] == "active"
        if active:
            report.add(f"systemd: {svc}", True, "running")
        else:
            if fix:
                _systemctl("restart", svc, timeout=30)
                time.sleep(2)
                r2 = _systemctl("is-active", svc)
                if r2["stdout"] == "active":
                    report.add(f"systemd: {svc}", True, f"🔧 restarted -> running")
                    report.note_fix(f"{svc} estaba fallado, reiniciado OK")
                else:
                    report.add(f"systemd: {svc}", False, f"falló restart: {r2['stdout']}")
            else:
                report.add(f"systemd: {svc}", False, r["stdout"] or "failed")

    for svc in _OPTIONAL_SERVICES:
        r = _systemctl("is-active", svc)
        if r["stdout"] != "active":
            report.add(f"systemd: {svc}", True, f"{r['stdout']} (optional)")
        else:
            report.add(f"systemd: {svc}", True, "running")


def check_postgresql(report: HealthReport) -> None:
    """Check PostgreSQL connectivity."""
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        report.add("PostgreSQL", True, "asyncpg not available (skip)")
        return

    try:
        import asyncio

        async def _probe() -> bool:
            try:
                conn = await asyncpg.connect(_DB_URL, timeout=5)
                try:
                    val = await conn.fetchval("SELECT 1")
                    return val == 1
                finally:
                    await conn.close()
            except Exception:
                return False

        ok = asyncio.run(_probe())
        report.add("PostgreSQL", ok, "connected OK" if ok else "connection failed")
    except Exception as e:
        report.add("PostgreSQL", False, str(e))


def check_gateway_http(report: HealthReport) -> None:
    """Check gateway HTTP observe endpoint."""
    # Gateway exposes HTTP on ports 9090-9094 per bot
    import socket

    for port in (9090, 9092, 9094):
        r = _run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://localhost:{port}/health"], timeout=5)
        if r["ok"]:
            report.add(f"Gateway http/:{port}", True, f"HTTP {r['stdout']}")
            return

    report.add("Gateway HTTP", False, "no endpoint responded on 9090-9094")


def check_last_log_entry(report: HealthReport) -> None:
    """Check that session_logs has recent entries (last 15 min)."""
    try:
        import asyncpg
        import asyncio

        async def _probe() -> dict:
            try:
                conn = await asyncpg.connect(_DB_URL, timeout=5)
                try:
                    row = await conn.fetchrow(
                        "SELECT session_id, ts FROM lina.session_logs ORDER BY ts DESC LIMIT 1"
                    )
                    if row:
                        age = (datetime.now(UTC) - row["ts"].replace(tzinfo=UTC)).total_seconds()
                        return {"ok": age < 3600, "detail": f"last log: {row['session_id']} ({age:.0f}s ago)", "age": age}
                    return {"ok": False, "detail": "no logs found"}
                finally:
                    await conn.close()
            except Exception as e:
                return {"ok": False, "detail": str(e)}

        result = asyncio.run(_probe())
        report.add("session_logs activity", result["ok"], result["detail"])
    except ImportError:
        report.add("session_logs activity", True, "asyncpg not available (skip)")


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LINA Self-Healthcheck")
    parser.add_argument("--fix", action="store_true", help="Auto-fix failed services")
    parser.add_argument("--notify", action="store_true", help="Send report via Telegram")
    args = parser.parse_args()

    report = HealthReport()

    # Run all checks
    check_systemd_services(report, fix=args.fix)
    check_postgresql(report)
    check_gateway_http(report)
    check_last_log_entry(report)

    # Save result
    report.save()

    # Print to stdout
    print(report.summary)

    # Notify via Telegram
    if args.notify and _SEND_BOT.exists():
        notify_msg = report.to_telegram()
        r = _run(
            ["python3", str(_SEND_BOT), "lina", notify_msg],
            timeout=30,
        )
        if r["ok"]:
            print(f"\n📨 Notificación enviada al grupo Comm")
        else:
            print(f"\n⚠️ Notificación falló: {r['stderr'][:100]}")

    return 0 if report.all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

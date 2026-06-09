#!/usr/bin/env python3
"""
goose-monitor — Monitor liviano de recursos en tiempo real.

Monitorea contenedores Docker cada 60s y alerta si algún container
supera el 80% de CPU o memoria. Las alertas se envían vía Comm
para que Goose (el agente) pueda tomar acción correctiva.

También monitorea:
  - Host RAM
  - Disco
  - Temperatura CPU

Uso:
    python3 bin/goose-monitor.py            # loop cada 60s
    python3 bin/goose-monitor.py --once     # una iteración
    python3 bin/goose-monitor.py --json     # salida JSON
    python3 bin/goose-monitor.py --interval 30  # cada 30s

Exit codes:
    0 = sin alertas
    1 = alertas activas
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

# ─── Config ──────────────────────────────────────────────────────────────────

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

# Umbrales de alerta
CPU_ALERT_PCT = 80
MEM_ALERT_PCT = 80
HOST_RAM_ALERT_PCT = 85
DISK_ALERT_PCT = 90
TEMP_ALERT_C = 85

# Cooldown entre alertas del mismo container (segundos)
ALERT_COOLDOWN_SEC = 300

POLL_INTERVAL = int(os.environ.get("GOOSE_MONITOR_INTERVAL", "60"))
COMM_SENDER = "goose-monitor"

logger = None  # Inicializado en main()


# ─── Estado de alertas (evita spam) ──────────────────────────────────────────

# Dict: alert_key → timestamp de última alerta
_last_alerts: dict[str, float] = {}


def _can_alert(key: str) -> bool:
    """Verifica si ha pasado el cooldown para esta alerta."""
    now = time.time()
    last = _last_alerts.get(key, 0.0)
    if now - last >= ALERT_COOLDOWN_SEC:
        _last_alerts[key] = now
        return True
    return False


# ─── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class ContainerMetric:
    name: str
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    mem_usage_mb: float = 0.0
    mem_limit_mb: float = 0.0
    running: bool = False
    healthy: bool = False


@dataclass
class HostMetric:
    ram_pct: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    cpu_temp_c: float = 0.0
    disk_pct: float = 0.0
    load_1m: float = 0.0


@dataclass
class MonitorReport:
    timestamp: str = ""
    alerts: list[str] = field(default_factory=list)
    containers: dict[str, ContainerMetric] = field(default_factory=dict)
    host: HostMetric = field(default_factory=HostMetric)
    has_alerts: bool = False


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_mem_mb(s: str) -> float:
    s = s.strip()
    if s.endswith("GiB"):
        return float(s.replace("GiB", "").strip()) * 1024
    elif s.endswith("MiB"):
        return float(s.replace("MiB", "").strip())
    elif s.endswith("KiB"):
        return float(s.replace("KiB", "").strip()) / 1024
    return 0.0


# ─── Collectors ──────────────────────────────────────────────────────────────


def collect_containers() -> dict[str, ContainerMetric]:
    """Obtiene métricas de todos los contenedores Docker."""
    results: dict[str, ContainerMetric] = {}

    try:
        stats_output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=15,
        )
        for line in stats_output.stdout.strip().split("\n"):
            if not line or line.startswith("NAME"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name = parts[0]
            cpu_str = parts[1].rstrip("%")
            mem_pct_str = parts[2].rstrip("%")
            mem_usage_str = parts[3]

            cm = ContainerMetric(name=name)
            cm.cpu_pct = float(cpu_str) if cpu_str else 0.0
            cm.mem_pct = float(mem_pct_str) if mem_pct_str else 0.0

            if " / " in mem_usage_str:
                used_part, limit_part = mem_usage_str.split(" / ")
                cm.mem_usage_mb = _parse_mem_mb(used_part)
                cm.mem_limit_mb = _parse_mem_mb(limit_part)

            results[name] = cm
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        logger.warning("docker stats failed: %s", e)

    # Verificar estado de cada container (running/healthy)
    try:
        ps_output = subprocess.run(
            ["docker", "ps", "--all", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in ps_output.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name, status = parts[0], parts[1]
            if name in results:
                results[name].running = "Up" in status
                results[name].healthy = "healthy" in status
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("docker ps failed: %s", e)

    return results


def collect_host() -> HostMetric:
    """Obtiene métricas del host."""
    hm = HostMetric()

    # RAM
    try:
        with open("/proc/meminfo") as f:
            meminfo = f.read()
        total_kb = 0
        available_kb = 0
        for line in meminfo.split("\n"):
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available_kb = int(line.split()[1])
        if total_kb > 0:
            used_kb = total_kb - available_kb
            hm.ram_pct = round(used_kb * 100 / total_kb, 1)
            hm.ram_total_gb = round(total_kb / 1048576, 1)
            hm.ram_used_gb = round(used_kb / 1048576, 1)
    except (FileNotFoundError, ValueError):
        pass

    # CPU temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            hm.cpu_temp_c = int(f.read().strip()) / 1000
    except (FileNotFoundError, ValueError):
        pass

    # Load
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            hm.load_1m = float(parts[0])
    except (FileNotFoundError, ValueError, IndexError):
        pass

    # Disco
    try:
        disk = subprocess.run(
            ["df", "-B1", "/"],
            capture_output=True, text=True, timeout=5,
        )
        lines = disk.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                total_bytes = int(parts[1])
                used_bytes = int(parts[2])
                if total_bytes > 0:
                    hm.disk_pct = round(used_bytes * 100 / total_bytes, 1)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

    return hm


# ─── Evaluación de alertas ──────────────────────────────────────────────────


def evaluate_alerts(
    containers: dict[str, ContainerMetric],
    host: HostMetric,
) -> list[str]:
    """Evalúa umbrales y genera lista de alertas (con cooldown)."""
    alerts: list[str] = []

    # ── Contenedores con CPU alto ─────────────────────────────────────
    for name, cm in containers.items():
        if cm.cpu_pct > CPU_ALERT_PCT:
            key = f"cpu:{name}"
            if _can_alert(key):
                msg = (
                    f"🔴 {name}: CPU {cm.cpu_pct:.0f}% "
                    f"(threshold {CPU_ALERT_PCT}%)"
                )
                alerts.append(msg)
                logger.warning("ALERT: %s", msg)

        if cm.mem_pct > MEM_ALERT_PCT:
            key = f"mem:{name}"
            if _can_alert(key):
                msg = (
                    f"🟠 {name}: MEM {cm.mem_pct:.0f}% "
                    f"({cm.mem_usage_mb:.0f}MB / {cm.mem_limit_mb:.0f}MB)"
                )
                alerts.append(msg)
                logger.warning("ALERT: %s", msg)

        if not cm.running and (name.startswith("lina") or name.startswith("docker-lina")):
            key = f"down:{name}"
            if _can_alert(key):
                msg = f"💀 {name}: container DOWN"
                alerts.append(msg)
                logger.warning("ALERT: %s", msg)

    # ── Host ──────────────────────────────────────────────────────────
    if host.ram_pct > HOST_RAM_ALERT_PCT:
        key = "host:ram"
        if _can_alert(key):
            msg = (
                f"🔴 Host RAM: {host.ram_pct:.0f}% "
                f"({host.ram_used_gb:.1f}G / {host.ram_total_gb:.1f}G)"
            )
            alerts.append(msg)
            logger.warning("ALERT: %s", msg)

    if host.cpu_temp_c > TEMP_ALERT_C:
        key = "host:temp"
        if _can_alert(key):
            msg = f"🔥 CPU temperature: {host.cpu_temp_c:.0f}°C"
            alerts.append(msg)
            logger.warning("ALERT: %s", msg)

    if host.disk_pct > DISK_ALERT_PCT:
        key = "host:disk"
        if _can_alert(key):
            msg = f"💾 Disk: {host.disk_pct:.0f}%"
            alerts.append(msg)
            logger.warning("ALERT: %s", msg)

    return alerts


# ─── Notificación vía Comm ───────────────────────────────────────────────────


async def notify_alerts(alerts: list[str], host: HostMetric):
    """Envía alertas a Goose vía Comm."""
    if not alerts:
        return

    try:
        conn = await asyncpg.connect(DB_URL, timeout=10)

        # Agrupar alertas en un solo mensaje
        lines = ["🚨 Goose Monitor — Alertas activas:"]
        lines.extend(f"  • {a}" for a in alerts)
        lines.append(f"  📊 Host: RAM {host.ram_pct:.0f}% | "
                     f"Temp {host.cpu_temp_c:.0f}°C | "
                     f"Disk {host.disk_pct:.0f}%")

        message = "\n".join(lines)

        # Enviar a goose (para que tome acción)
        await conn.execute(
            """INSERT INTO comm_messages (sender, destination, message, status)
               VALUES ($1, 'goose', $2, 'sent')""",
            COMM_SENDER,
            message,
        )

        # También a fede si es crítico
        critical = any("🔴" in a or "💀" in a for a in alerts)
        if critical:
            await conn.execute(
                """INSERT INTO comm_messages (sender, destination, message, status)
                   VALUES ($1, 'fede', $2, 'sent')""",
                COMM_SENDER,
                f"🚨 {len(alerts)} alerta(s) activa(s). Goose está siendo notificado.",
            )

        await conn.close()
        logger.info("📬 %s alerta(s) enviada(s) vía Comm", len(alerts))
    except Exception as e:
        logger.error("❌ Error notificando alertas: %s", e)


# ─── Main Loop ───────────────────────────────────────────────────────────────


async def main_loop(once: bool = False, interval: int = POLL_INTERVAL):
    """Loop principal de monitoreo."""
    loop_count = 0

    while True:
        loop_count += 1
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logger.debug("📊 Monitor check #%s (%s)", loop_count, now)

        containers = collect_containers()
        host = collect_host()
        alerts = evaluate_alerts(containers, host)

        report = MonitorReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            alerts=alerts,
            containers=containers,
            host=host,
            has_alerts=len(alerts) > 0,
        )

        # Notificar si hay alertas
        if alerts:
            await notify_alerts(alerts, host)
            logger.info("⚠️  %s alerta(s) activa(s)", len(alerts))
        else:
            logger.debug("✅ Sin alertas")

        if once:
            break

        await asyncio.sleep(interval)


async def main():
    parser = argparse.ArgumentParser(
        description="Goose Monitor — Monitor liviano de recursos en tiempo real"
    )
    parser.add_argument("--once", action="store_true", help="Una sola iteración")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL,
                        help=f"Intervalo en segundos (default: {POLL_INTERVAL})")
    args = parser.parse_args()

    global logger
    import logging
    logging.basicConfig(
        level=getattr(logging, os.environ.get("GOOSE_MONITOR_LOG_LEVEL", "INFO")),
        format="%(asctime)s %(levelname)s [goose-monitor] %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("goose-monitor")

    if args.json:
        # Modo JSON: una iteración, salida estructurada
        containers = collect_containers()
        host = collect_host()
        alerts = evaluate_alerts(containers, host)
        report = MonitorReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            alerts=alerts,
            containers=containers,
            host=host,
            has_alerts=len(alerts) > 0,
        )
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
        return

    logger.info("🚀 Goose Monitor iniciado (intervalo: %ds)", args.interval)
    logger.info("Umbrales: CPU>%s%% | MEM>%s%% | RAM>%s%% | Disk>%s%% | Temp>%s°C",
                CPU_ALERT_PCT, MEM_ALERT_PCT, HOST_RAM_ALERT_PCT,
                DISK_ALERT_PCT, TEMP_ALERT_C)

    await main_loop(once=args.once, interval=args.interval)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
goose-health — Sistema de monitoreo integral del ecosistema LINA.

Ejecuta health checks completos, evalúa umbrales, auto-repara cuando es
posible, y notifica vía Comm (DB → Telegram por número de teléfono).

Flujo:
  1. Checkea todos los bots (LINA, Cline, Gemma, Goose)
  2. Checkea contenedores Docker (status, CPU, memoria)
  3. Checkea servicios systemd (gateways, bridge)
  4. Checkea recursos del host (CPU, RAM, disco)
  5. Evalúa umbrales críticos
  6. Auto-repara si es posible (restart container/service)
  7. Reporta resultados vía Comm

Uso:
    python3 bin/goose-health.py            # chequeo único
    python3 bin/goose-health.py --watch    # loop cada 5 min
    python3 bin/goose-health.py --json     # salida JSON

Exit codes:
    0 = healthy
    1 = degraded (problemas no críticos)
    2 = critical (bots/DB/containers caídos)
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
import httpx

# ─── Config ──────────────────────────────────────────────────────────────────

# Ruta del proyecto LINA
LINA_DIR = Path(__file__).resolve().parent.parent
BOT_HEALTH_SCRIPT = LINA_DIR / "bin" / "bot_health.py"

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

BOTS = {
    "lina": {
        "url": os.environ.get("LINA_GOOSED_URL", "https://localhost:3000"),
        "port": 3000,
        "secret": os.environ.get("LINA_GOOSED_SECRET", ""),
        "service": None,  # Docker container
    },
    "cline": {
        "url": os.environ.get("CLINE_GOOSED_URL", "https://localhost:3001"),
        "port": 3001,
        "secret": os.environ.get("CLINE_GOOSED_SECRET", ""),
        "service": None,
    },
    "gemma": {
        "url": os.environ.get("GEMMA_GOOSED_URL", "https://localhost:3002"),
        "port": 3002,
        "secret": os.environ.get("GEMMA_GOOSED_SECRET", ""),
        "service": None,
    },
    # Goose local se checkea aparte (puerto 42359)
}

GOOSE_GOOSED_URL = "https://localhost:42359"
GOOSE_GOOSED_SECRET = os.environ.get("GOOSE_SERVER__SECRET_KEY", "")

# Servicios systemd a monitorear
# NOTA: goose-gateway.service está deprecado — el gateway de goose se consolidó
# en lina-gateway-host.service (multi-bot: LINA + Cline + Gemma + goose vía comm)
SYSTEMD_SERVICES = [
    "lina-gateway-host.service",
    "lina-comm-bridge.service",
    "comm-svc.service",
]

# Contenedores Docker a monitorear (los de lina)
DOCKER_CONTAINERS = [
    # Core — deben estar siempre UP
    "lina-goosed",
    "docker-lina-mcp-gateway-1",
    "docker-lina-db-1",
    # MCPs
    "docker-lina-mcp-secrets-1",
    "docker-lina-mcp-fs-safe-1",
    "docker-lina-mcp-shell-policy-1",
    "docker-lina-mcp-systemd-user-1",
    "docker-lina-mcp-moodle-1",
    "docker-lina-mcp-db-1",
    "docker-lina-mcp-knowledge-1",
    "docker-lina-mcp-github-1",
    "docker-lina-mcp-gitlab-1",
    "docker-lina-mcp-gcalendar-1",
    "docker-lina-mcp-gns3-1",
    "docker-lina-mcp-orchestrator-1",
    # Infra
    "docker-lina-backup-1",
    "docker-lina-docker-proxy-1",
    # Opcionales (perfiles) — monitorear pero no alarmar si no existen
    "cline-goosed",
    "gemma-goosed",
]

# Containers que DEBEN estar siempre corriendo (críticos)
CRITICAL_CONTAINERS = [
    "lina-goosed",
    "docker-lina-db-1",
    "docker-lina-mcp-gateway-1",
]

# Containers opcionales (según perfil Docker) — no alarmar si faltan
OPTIONAL_CONTAINERS = [
    "cline-goosed",
    "gemma-goosed",
]

# Umbrales
CPU_WARN_PCT = 80
MEM_WARN_PCT = 80
DISK_WARN_PCT = 85
TEMP_WARN_C = 80
HOST_RAM_WARN_PCT = 85

HTTP_TIMEOUT = 10.0
POLL_INTERVAL = 300  # 5 minutos en modo --watch

logger = None  # Se inicializa en main()


# ─── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class BotStatus:
    name: str
    port: int
    online: bool = False
    latency_ms: float = 0.0
    sessions: int = 0
    error: str = ""


@dataclass
class ContainerStatus:
    name: str
    running: bool = False
    healthy: bool = False
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    mem_usage_mb: float = 0.0
    mem_limit_mb: float = 0.0
    restart_count: int = 0
    error: str = ""


@dataclass
class SystemdStatus:
    name: str
    active: bool = False
    state: str = ""
    error: str = ""


@dataclass
class HostStatus:
    ram_pct: float = 0.0
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    cpu_temp_c: float = 0.0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    disk_pct: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    uptime: str = ""


@dataclass
class DatabaseStatus:
    connected: bool = False
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class HealthReport:
    timestamp: str = ""
    overall: str = "unknown"  # healthy | degraded | critical
    bots: dict[str, BotStatus] = field(default_factory=dict)
    containers: dict[str, ContainerStatus] = field(default_factory=dict)
    systemd: dict[str, SystemdStatus] = field(default_factory=dict)
    host: HostStatus = field(default_factory=HostStatus)
    database: DatabaseStatus = field(default_factory=DatabaseStatus)
    actions_taken: list[str] = field(default_factory=list)
    summary: str = ""


# ─── Health Checks ──────────────────────────────────────────────────────────


async def check_bot(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    port: int,
    secret: str,
) -> BotStatus:
    """Checkea un goosed vía /status."""
    result = BotStatus(name=name, port=port)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-secret-key"] = secret

    try:
        start = time.monotonic()
        resp = await client.get(f"{url}/status", headers=headers, timeout=HTTP_TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)

        if resp.status_code == 200:
            result.online = True
            try:
                data = resp.json()
                if isinstance(data, dict):
                    result.sessions = len(data.get("sessions", {}))
            except (json.JSONDecodeError, AttributeError):
                pass
        else:
            result.error = f"HTTP {resp.status_code}"
    except httpx.ConnectError:
        result.error = "connection refused"
    except httpx.TimeoutException:
        result.error = "timeout"
    except Exception as e:
        result.error = str(e)[:120]

    return result


async def check_database() -> DatabaseStatus:
    """Checkea conexión a PostgreSQL."""
    result = DatabaseStatus()
    try:
        start = time.monotonic()
        conn = await asyncpg.connect(DB_URL, timeout=HTTP_TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)
        val = await conn.fetchval("SELECT 1")
        result.connected = (val == 1)
        await conn.close()
    except Exception as e:
        result.error = str(e)[:120]
    return result


def check_containers() -> dict[str, ContainerStatus]:
    """Checkea contenedores Docker via CLI."""
    results: dict[str, ContainerStatus] = {}

    # Obtener stats de Docker
    try:
        stats_output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=15,
        )
        stats: dict[str, dict] = {}
        for line in stats_output.stdout.strip().split("\n"):
            if not line or line.startswith("NAME"):
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                name = parts[0]
                cpu_str = parts[1].rstrip("%")
                mem_pct_str = parts[2].rstrip("%")
                mem_usage_str = parts[3]
                stats[name] = {
                    "cpu_pct": float(cpu_str) if cpu_str else 0.0,
                    "mem_pct": float(mem_pct_str) if mem_pct_str else 0.0,
                    "mem_usage": mem_usage_str,
                }
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        for cname in DOCKER_CONTAINERS:
            results[cname] = ContainerStatus(
                name=cname, running=False, error=f"docker stats failed: {e}"
            )
        return results

    # Obtener estado de containers
    try:
        ps_output = subprocess.run(
            ["docker", "ps", "--all", "--format",
             "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=15,
        )
        ps_map: dict[str, str] = {}
        for line in ps_output.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                ps_map[parts[0]] = parts[1]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        for cname in DOCKER_CONTAINERS:
            results[cname] = ContainerStatus(
                name=cname, running=False, error=f"docker ps failed: {e}"
            )
        return results

    # Armar resultados
    for cname in DOCKER_CONTAINERS:
        cs = ContainerStatus(name=cname)
        status_str = ps_map.get(cname, "")

        if "Up" in status_str:
            cs.running = True
            cs.healthy = "healthy" in status_str or "(healthy)" in status_str
        elif "Exited" in status_str:
            cs.running = False
            cs.error = status_str
        else:
            cs.running = False
            cs.error = status_str if status_str else "not found"

        # Agregar stats si están disponibles
        if cname in stats:
            cs.cpu_pct = stats[cname]["cpu_pct"]
            cs.mem_pct = stats[cname]["mem_pct"]
            # Parsear mem_usage: "82.06MiB / 2GiB"
            mem_str = stats[cname]["mem_usage"]
            if " / " in mem_str:
                used_part, limit_part = mem_str.split(" / ")
                cs.mem_usage_mb = _parse_mem_mb(used_part)
                cs.mem_limit_mb = _parse_mem_mb(limit_part)

        results[cname] = cs

    return results


def _parse_mem_mb(s: str) -> float:
    """Convierte '82.06MiB' o '2GiB' a MB."""
    s = s.strip()
    if s.endswith("GiB"):
        return float(s.replace("GiB", "").strip()) * 1024
    elif s.endswith("MiB"):
        return float(s.replace("MiB", "").strip())
    elif s.endswith("KiB"):
        return float(s.replace("KiB", "").strip()) / 1024
    return 0.0


def check_systemd_services() -> dict[str, SystemdStatus]:
    """Checkea servicios systemd via systemctl --user.
    
    Si no hay D-Bus session (entorno sin systemd), intenta detectar
    el estado del proceso como fallback.
    """
    results: dict[str, SystemdStatus] = {}
    no_bus = False

    for svc in SYSTEMD_SERVICES:
        ss = SystemdStatus(name=svc)
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            state = r.stdout.strip()
            ss.state = state
            ss.active = (state == "active")

            if "No medium" in (r.stderr or "") or "not a tty" in (r.stderr or ""):
                no_bus = True
        except FileNotFoundError:
            ss.error = "systemctl not found"
        except subprocess.TimeoutExpired:
            ss.error = "timeout"
        except Exception as e:
            ss.error = str(e)[:120]

        results[svc] = ss

    # Fallback: si no hay D-Bus, detectar por proceso
    if no_bus:
        logger.info("No D-Bus session — detectando servicios por proceso")
        # Mapa: nombre servicio → patrón de proceso
        svc_procs = {
            "lina-gateway-host.service": "lina-gateway-entrypoint",
            "lina-comm-bridge.service": "comm_bridge.py",
            "comm-svc.service": "comm-svc.py",
        }
        try:
            ps_out = subprocess.run(
                ["ps", "aux"],
                capture_output=True, text=True, timeout=5,
            )
            for svc_name, pattern in svc_procs.items():
                if pattern in ps_out.stdout:
                    results[svc_name].active = True
                    results[svc_name].state = "running (process)"
                    results[svc_name].error = ""
                    logger.info("  ✅ %s detected via process (%s)", svc_name, pattern)
                else:
                    if not results[svc_name].error:
                        results[svc_name].error = "process not found"
                        results[svc_name].state = "inactive"
        except Exception as e:
            logger.warning("Process detection fallback failed: %s", e)

    return results


def check_host() -> HostStatus:
    """Checkea recursos del host."""
    hs = HostStatus()

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
            hs.ram_pct = round(used_kb * 100 / total_kb, 1)
            hs.ram_total_gb = round(total_kb / 1048576, 1)
            hs.ram_used_gb = round(used_kb / 1048576, 1)
    except (FileNotFoundError, ValueError, IndexError):
        pass

    # CPU temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            hs.cpu_temp_c = int(f.read().strip()) / 1000
    except (FileNotFoundError, ValueError):
        pass

    # Load average
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            hs.load_1m = float(parts[0])
            hs.load_5m = float(parts[1])
            hs.load_15m = float(parts[2])
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
                hs.disk_total_gb = round(total_bytes / (1024**3), 1)
                hs.disk_used_gb = round(used_bytes / (1024**3), 1)
                if total_bytes > 0:
                    hs.disk_pct = round(used_bytes * 100 / total_bytes, 1)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_secs = float(f.read().split()[0])
            days = int(uptime_secs // 86400)
            hours = int((uptime_secs % 86400) // 3600)
            mins = int((uptime_secs % 3600) // 60)
            hs.uptime = f"{days}d {hours}h {mins}m"
    except (FileNotFoundError, ValueError, IndexError):
        pass

    return hs


# ─── Auto-healing ────────────────────────────────────────────────────────────


async def auto_heal(report: HealthReport) -> list[str]:
    """Intenta auto-reparar problemas detectados.
    
    Retorna lista de acciones tomadas.
    """
    actions: list[str] = []

    # ── 1. Reiniciar contenedores caídos (solo críticos) ────────────
    for cname, cs in report.containers.items():
        if not cs.running and cname in CRITICAL_CONTAINERS:
            logger.warning("🔄 Auto-heal: restarting container %s", cname)
            try:
                r = subprocess.run(
                    ["docker", "restart", cname],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode == 0:
                    actions.append(f"restarted container {cname}")
                    logger.info("✅ Container %s restarted successfully", cname)
                else:
                    logger.error("❌ Failed to restart %s: %s", cname, r.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.error("❌ Timeout restarting %s", cname)
            except Exception as e:
                logger.error("❌ Error restarting %s: %s", cname, e)
            await asyncio.sleep(2)  # Pausa entre restarts

    # ── 2. Verificar si podemos hacer systemctl (D-Bus disponible) ──
    _can_systemctl = True
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                          capture_output=True, timeout=3)
        if r.returncode != 0 or "No medium" in (r.stderr or ""):
            _can_systemctl = False
            logger.info("No D-Bus session — skipping systemctl auto-heal")
    except Exception:
        _can_systemctl = False

    # ── 3. Reiniciar servicios systemd caídos (solo si hay D-Bus) ──
    if _can_systemctl:
        for svc_name, ss in report.systemd.items():
            if not ss.active:
                logger.warning("🔄 Auto-heal: restarting service %s", svc_name)
                try:
                    r = subprocess.run(
                        ["systemctl", "--user", "restart", svc_name],
                        capture_output=True, text=True, timeout=15,
                    )
                    if r.returncode == 0:
                        actions.append(f"restarted service {svc_name}")
                        logger.info("✅ Service %s restarted", svc_name)
                    else:
                        logger.error("❌ Failed to restart %s: %s", svc_name, r.stderr[:200])
                except subprocess.TimeoutExpired:
                    logger.error("❌ Timeout restarting %s", svc_name)
                except Exception as e:
                    logger.error("❌ Error restarting %s: %s", svc_name, e)
                await asyncio.sleep(2)
    else:
        logger.info("⚠️ Auto-heal: systemctl no disponible, saltando reparación de servicios")

    # ── 3. Rebuild lina-goosed si está caído persistentemente ────────
    lina_cs = report.containers.get("lina-goosed")
    if lina_cs and not lina_cs.running:
        # Verificar si ya se intentó restart antes en este mismo ciclo
        already_restarted = any("lina-goosed" in a for a in actions)
        if already_restarted:
            # Segundo intento fallido → rebuild
            logger.warning("🔧 Auto-heal: rebuilding lina-goosed image...")
            try:
                r = subprocess.run(
                    ["docker", "compose", "-f",
                     f"{LINA_DIR}/deploy/docker/docker-compose.yml",
                     "build", "lina-goosed"],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    actions.append("rebuilt lina-goosed image")
                    logger.info("✅ lina-goosed image rebuilt")
                    # Arrancar de nuevo
                    await asyncio.sleep(2)
                    r2 = subprocess.run(
                        ["docker", "compose", "-f",
                         f"{LINA_DIR}/deploy/docker/docker-compose.yml",
                         "up", "-d", "lina-goosed"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if r2.returncode == 0:
                        actions.append("started lina-goosed after rebuild")
                        logger.info("✅ lina-goosed started after rebuild")
                else:
                    logger.error("❌ Rebuild failed: %s", r.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.error("❌ Timeout rebuilding lina-goosed")
            except Exception as e:
                logger.error("❌ Error rebuilding lina-goosed: %s", e)

    return actions


# ─── Evaluación de umbrales ──────────────────────────────────────────────────


def evaluate_thresholds(report: HealthReport) -> tuple[str, str]:
    """Evalúa umbrales y devuelve (overall_status, summary_text)."""
    issues: list[str] = []
    warnings: list[str] = []
    ok_items: list[str] = []

    # ── Bots ──────────────────────────────────────────────────────────
    bots_online = sum(1 for b in report.bots.values() if b.online)
    total_bots = len(report.bots)
    if bots_online == total_bots:
        ok_items.append(f"bots: {bots_online}/{total_bots} online")
    elif bots_online >= total_bots / 2:
        warnings.append(f"bots: {bots_online}/{total_bots} online (degradado)")
    else:
        issues.append(f"bots: {bots_online}/{total_bots} online (crítico)")
        # Detallar cuáles están caídos
        down_bots = [n for n, b in report.bots.items() if not b.online]
        for name in down_bots:
            bot = report.bots[name]
            issues.append(f"  • {name}:{bot.port} — {bot.error or 'offline'}")

    # ── Goose local ───────────────────────────────────────────────────
    goose_bot = report.bots.get("goose")
    if goose_bot and goose_bot.online:
        ok_items.append(f"goose local: online ({goose_bot.latency_ms:.0f}ms)")
    elif goose_bot:
        issues.append(f"goose local: {goose_bot.error}")
        actions = _try_fix_goose()
        if actions:
            report.actions_taken.extend(actions)

    # ── Database ──────────────────────────────────────────────────────
    if report.database.connected:
        ok_items.append(f"DB: conectada ({report.database.latency_ms:.0f}ms)")
    else:
        issues.append(f"DB: {report.database.error}")

    # ── Containers ────────────────────────────────────────────────────
    containers_running = sum(1 for c in report.containers.values() if c.running)
    total_containers = len(report.containers)
    ok_items.append(f"containers: {containers_running}/{total_containers} running")

    # Containers con uso alto de CPU/memoria
    for cname, cs in report.containers.items():
        if cs.running:
            if cs.cpu_pct > CPU_WARN_PCT:
                warnings.append(f"  • {cname}: CPU {cs.cpu_pct:.0f}% (threshold {CPU_WARN_PCT}%)")
            if cs.mem_pct > MEM_WARN_PCT:
                warnings.append(f"  • {cname}: MEM {cs.mem_pct:.0f}% (threshold {MEM_WARN_PCT}%)")
            # Solo checkear healthcheck si el container tiene uno
            # (algunos containers como backup y docker-proxy no tienen healthcheck y siempre son "no healthy")
            if not cs.healthy and cname in CRITICAL_CONTAINERS:
                warnings.append(f"  • {cname}: running pero NO healthy")

    # Containers caídos (críticos)
    down_critical = [n for n, c in report.containers.items()
                     if not c.running and n in CRITICAL_CONTAINERS]
    if down_critical:
        for name in down_critical:
            cs = report.containers[name]
            issues.append(f"  • {name}: {cs.error or 'down'}")
    
    # Containers opcionales caídos (warning, no critical)
    down_optional = [n for n, c in report.containers.items()
                     if not c.running and n in OPTIONAL_CONTAINERS]
    if down_optional:
        for name in down_optional:
            warnings.append(f"  • {name}: not deployed (optional container)")

    # ── Systemd services ──────────────────────────────────────────────
    svc_active = sum(1 for s in report.systemd.values() if s.active)
    total_svc = len(report.systemd)
    ok_items.append(f"services: {svc_active}/{total_svc} active")

    down_svc = [n for n, s in report.systemd.items() if not s.active]
    if down_svc:
        for name in down_svc:
            svc = report.systemd[name]
            issues.append(f"  • {name}: {svc.state or svc.error or 'inactive'}")

    # ── Host resources ────────────────────────────────────────────────
    hs = report.host
    if hs.ram_pct > HOST_RAM_WARN_PCT:
        issues.append(f"host RAM: {hs.ram_pct:.0f}% (threshold {HOST_RAM_WARN_PCT}%)")
    if hs.cpu_temp_c > TEMP_WARN_C:
        warnings.append(f"CPU temp: {hs.cpu_temp_c:.0f}°C")
    if hs.disk_pct > DISK_WARN_PCT:
        issues.append(f"disk: {hs.disk_pct:.0f}% (threshold {DISK_WARN_PCT}%)")

    # ── Overall status ────────────────────────────────────────────────
    if issues:
        overall = "critical"
    elif warnings:
        overall = "degraded"
    else:
        overall = "healthy"

    # ── Summary text ──────────────────────────────────────────────────
    lines = [f"[🦆 Goose Health] Estado: {overall.upper()}"]
    lines.append(f"  📊 {len(ok_items)} checks OK")
    if warnings:
        lines.append(f"  ⚠️  {len(warnings)} warnings:")
        lines.extend(warnings)
    if issues:
        lines.append(f"  ❌ {len(issues)} issues:")
        lines.extend(issues)
    if report.actions_taken:
        lines.append(f"  🔧 Acciones: {', '.join(report.actions_taken)}")
    lines.append(f"  ⏱️  {report.timestamp[:19]}")

    summary = "\n".join(lines)
    return overall, summary


def _try_fix_goose() -> list[str]:
    """Intenta reparar goosed local si está caído."""
    actions = []
    try:
        # Verificar si el proceso goosed existe
        r = subprocess.run(
            ["pgrep", "-f", "goosed agent"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            # No está corriendo → restart via systemd
            logger.warning("🔄 Auto-heal: goosed local no está corriendo")
            r2 = subprocess.run(
                ["systemctl", "--user", "restart", "goose.service"],
                capture_output=True, text=True, timeout=15,
            )
            if r2.returncode == 0:
                actions.append("restarted goosed local (goose.service)")
                logger.info("✅ goosed local restarted")
            else:
                logger.error("❌ Failed to restart goosed local: %s", r2.stderr[:200])
    except Exception as e:
        logger.error("❌ Error fixing goosed: %s", e)
    return actions


# ─── Comm notification ───────────────────────────────────────────────────────


async def notify_comm(report: HealthReport, overall: str, summary: str):
    """Envía notificación vía Comm (DB → Telegram por número de teléfono).
    
    Estrategia:
    - healthy:    mensaje corto, solo para goose (logging interno)
    - degraded:   mensaje a goose + fede (por si necesita atención)
    - critical:   mensaje a goose + fede + alerta audible
    """
    try:
        conn = await asyncpg.connect(DB_URL, timeout=10)

        # Siempre enviar a goose (para su registro interno)
        await conn.execute(
            """INSERT INTO comm_messages (sender, destination, message, status)
               VALUES ('goose-health', 'goose', $1, 'sent')""",
            summary,
        )

        # Solo avisar a fede si es CRÍTICO (no spam por degradado)
        if overall == "critical":
            fede_msg = (
                f"🚨 Goose Health CRÍTICO\n"
                f"{summary.split('⏱️')[0][:500]}"
            )
            await conn.execute(
                """INSERT INTO comm_messages (sender, destination, message, status)
                   VALUES ('goose-health', 'fede', $1, 'sent')""",
                fede_msg,
            )

        await conn.close()
    except Exception as e:
        logger.error("❌ Failed to notify via Comm: %s", e)


# ─── Report builders ─────────────────────────────────────────────────────────


def build_json(report: HealthReport) -> str:
    """Construye reporte JSON."""
    d: dict[str, Any] = {
        "timestamp": report.timestamp,
        "overall": report.overall,
        "summary": report.summary,
        "actions_taken": report.actions_taken,
        "bots": {},
        "containers": {},
        "systemd": {},
        "host": asdict(report.host),
        "database": asdict(report.database),
    }

    for name, bot in report.bots.items():
        d["bots"][name] = asdict(bot)
    for name, cs in report.containers.items():
        d["containers"][name] = asdict(cs)
    for name, ss in report.systemd.items():
        d["systemd"][name] = asdict(ss)

    return json.dumps(d, indent=2, ensure_ascii=False)


def build_text(report: HealthReport) -> str:
    """Construye reporte en texto."""
    return report.summary


# ─── Main ──────────────────────────────────────────────────────────────--------


async def collect() -> HealthReport:
    """Ejecuta todos los checks y devuelve un HealthReport completo."""
    timestamp = datetime.now(timezone.utc).isoformat()
    report = HealthReport(timestamp=timestamp)

    # ── Bots ──────────────────────────────────────────────────────────
    async with httpx.AsyncClient(verify=False) as client:
        bot_tasks = {}
        for name, cfg in BOTS.items():
            bot_tasks[name] = check_bot(
                client, name, cfg["url"], cfg["port"], cfg["secret"]
            )
        # Goose local (puerto 42359)
        bot_tasks["goose"] = check_bot(
            client, "goose", GOOSE_GOOSED_URL, 42359, GOOSE_GOOSED_SECRET
        )
        bot_results = await asyncio.gather(*bot_tasks.values())
        report.bots = dict(zip(bot_tasks.keys(), bot_results))

    # ── Database ──────────────────────────────────────────────────────
    report.database = await check_database()

    # ── Containers ────────────────────────────────────────────────────
    report.containers = check_containers()

    # ── Systemd services ──────────────────────────────────────────────
    report.systemd = check_systemd_services()

    # ── Host resources ────────────────────────────────────────────────
    report.host = check_host()

    # ── Evaluate + auto-heal ──────────────────────────────────────────
    report.overall, report.summary = evaluate_thresholds(report)

    if report.overall != "healthy":
        actions = await auto_heal(report)
        report.actions_taken = actions
        # Re-evaluar después de auto-heal (opcional, podría re-checkear)
        if actions:
            report.summary += "\n\n🔧 Acciones tomadas:\n" + "\n".join(f"  • {a}" for a in actions)

    return report


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Goose Health — Monitoreo integral del ecosistema LINA"
    )
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    parser.add_argument("--watch", action="store_true", help="Loop cada 5 min")
    parser.add_argument("--interval", type=int, default=300, help="Intervalo en segundos")
    parser.add_argument("--no-notify", action="store_true", help="No enviar notificaciones Comm")
    args = parser.parse_args()

    # Configurar logging
    global logger
    import logging
    logging.basicConfig(
        level=getattr(logging, os.environ.get("GOOSE_HEALTH_LOG_LEVEL", "INFO")),
        format="%(asctime)s %(levelname)s [goose-health] %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("goose-health")

    loop_count = 0
    while True:
        loop_count += 1
        if args.watch:
            logger.info("📊 Health check #%s", loop_count)

        report = await collect()

        if args.json:
            print(build_json(report))
        else:
            print(build_text(report))
            print(f"\n{'─' * 50}")

        # Notificar vía Comm
        if not args.no_notify:
            await notify_comm(report, report.overall, report.summary)
            if report.overall != "healthy":
                logger.info("📬 Notificación enviada vía Comm")
            if report.actions_taken:
                logger.info("🔧 Acciones tomadas: %s", ", ".join(report.actions_taken))

        if not args.watch:
            break

        await asyncio.sleep(args.interval)

    return {"healthy": 0, "degraded": 1, "critical": 2}.get(report.overall, 2)


def run():
    """Entrypoint CLI."""
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


if __name__ == "__main__":
    run()

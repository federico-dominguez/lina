"""
lina-dashboard — Unified monitoring dashboard for LINA ecosystem.

Tracks:
- Bot status (online/offline, session count) via observe ports
- Active Telegram sessions aggregated from all gateways
- Docker container health (status, ports, uptime)
- MCP service health (HTTP endpoint checks)

Usage:
    python -m lina_dashboard [--port 8090]
"""

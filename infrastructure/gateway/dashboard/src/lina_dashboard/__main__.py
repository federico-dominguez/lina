"""Entry point: python -m lina_dashboard."""

import argparse
import asyncio
import logging
import os
import signal

from .collectors import DashboardCollector
from .server import DashboardServer

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="LINA Monitoring Dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8090")))
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "0.0.0.0"))
    parser.add_argument(
        "--bot-ports",
        type=int,
        nargs="*",
        default=[9090, 9091, 9092, 9093],
        help="Observe ports for Goose(9091), CLINE(9092), LINA(9093), Gemma(9090)",
    )
    parser.add_argument(
        "--bot-host",
        default=os.environ.get("BOT_HOST", "localhost"),
        help="Hostname for bot observe endpoints (use host.docker.internal when in container)",
    )
    parser.add_argument(
        "--docker-url",
        default=os.environ.get("DOCKER_URL", "http://localhost:2375"),
        help="Docker socket proxy URL",
    )
    parser.add_argument(
        "--mcp-host",
        default=os.environ.get("MCP_HOST", "localhost"),
        help="MCP nginx gateway host",
    )
    args = parser.parse_args()

    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    collector = DashboardCollector(
        bot_ports=args.bot_ports,
        docker_url=args.docker_url,
        mcp_host=args.mcp_host,
        bot_host=args.bot_host,
    )
    server = DashboardServer(collector, host=args.host, port=args.port)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda *_: stop.set())

    async def _shutdown_watch() -> None:
        await stop.wait()
        logger.info("Shutting down dashboard...")
        await server.stop()

    loop.create_task(_shutdown_watch(), name="shutdown")
    await server.start()


if __name__ == "__main__":
    main()

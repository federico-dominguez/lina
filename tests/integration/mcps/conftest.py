"""
Shared helper for E2E integration tests of LINA MCPs over stdio.

Usage:
    from tests.conftest import McpStdioClient

    client = McpStdioClient("lina-fs-safe", env={"MCP_TRANSPORT": "stdio"})
    with client:
        result = client.call("initialize", {...})
        tools = client.call("tools/list", {})
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any


class McpStdioClient:
    """Spawns an MCP server subprocess and communicates via JSON-RPC over stdio."""

    def __init__(self, cmd: str, cwd: str | None = None, env: dict[str, str] | None = None, timeout: float = 10.0):
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._id = 0

    def __enter__(self) -> "McpStdioClient":
        import os
        proc_env = os.environ.copy()
        if self.env:
            proc_env.update(self.env)
        self._proc = subprocess.Popen(
            ["uv", "run", self.cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=proc_env,
            text=True,
            cwd=self.cwd,
        )
        return self

    def __exit__(self, *_) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _send(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(msg + "\n")
        self._proc.stdin.flush()
        # Read response with timeout
        import select
        import sys
        start = time.monotonic()
        while time.monotonic() - start < self.timeout:
            assert self._proc.stdout
            ready, _, _ = select.select([self._proc.stdout], [], [], 0.1)
            if ready:
                line = self._proc.stdout.readline()
                if line.strip():
                    resp = json.loads(line)
                    if resp.get("id") == self._id:
                        if "error" in resp:
                            raise RuntimeError(f"MCP error: {resp['error']}")
                        return resp.get("result")
        raise TimeoutError(f"No response from {self.cmd} within {self.timeout}s")

    def initialize(self) -> dict:
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "0.0.1"},
        })
        # Required by MCP spec: send notifications/initialized after initialize response
        assert self._proc and self._proc.stdin
        notification = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self._proc.stdin.write(notification + "\n")
        self._proc.stdin.flush()
        return result

    def tools_list(self) -> list[dict]:
        result = self._send("tools/list", {})
        return result.get("tools", [])

    def tools_call(self, name: str, arguments: dict) -> Any:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        return result

"""goosed HTTP/SSE client.

Communicates with the goosed server via its REST API:
  POST /reply  — send a message, receive SSE stream of MessageEvent
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Track tool call IDs to names across SSE events.
# key: call_id (e.g. "call_00_xxx"), value: tool_name (e.g. "shell")
# Periodically pruned to prevent orphaned entries from memory leaks.
_tool_call_names: dict[str, str] = {}
_LAST_TOOL_CALL_CLEANUP: float = 0.0


def _cleanup_tool_call_names() -> None:
    """Remove entries older than 10 minutes (orphaned tool calls)."""
    global _LAST_TOOL_CALL_CLEANUP  # noqa: PLW0603
    now = time.time()
    if now - _LAST_TOOL_CALL_CLEANUP < 600:
        return
    _LAST_TOOL_CALL_CLEANUP = now
    n = len(_tool_call_names)
    if n > 100:
        _tool_call_names.clear()
        logger.info("Cleared %d orphaned tool_call_names entries", n)


# ─── MessageEvent types (mirrors goose-server reply.rs) ─────────────────────


class EventType(StrEnum):
    MESSAGE = "Message"
    FINISH = "Finish"
    ERROR = "Error"
    PING = "Ping"
    NOTIFICATION = "Notification"
    UPDATE_CONVERSATION = "UpdateConversation"
    ACTIVE_REQUESTS = "ActiveRequests"


@dataclass
class MessageContent:
    content_type: str  # "text", "thinking", "tool_request", "tool_response", ...
    text: str = ""
    thinking: str = ""
    tool_name: str = ""
    args_preview: str = ""
    result_preview: str = ""
    success: bool | None = None


@dataclass
class TokenState:
    """Token usage and cost from goosed's token_state field (real values from DeepSeek)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Monotonic per-session accumulated cost in USD (not per-turn — caller must diff)
    accumulated_cost: float = 0.0


@dataclass
class MessageEvent:
    event_type: EventType
    # For Message events
    role: str = ""
    contents: list[MessageContent] = field(default_factory=list)
    # For Finish events
    finish_reason: str = ""
    token_state: TokenState | None = None
    # For Error events
    error: str = ""


def _parse_tool_args(args: Any) -> str:
    """Extract a preview string from tool arguments."""
    if not isinstance(args, dict):
        return str(args)[:80]
    for key in ("command", "path", "query", "input", "code"):
        if val := args.get(key):
            raw = str(val)
            collapsed = " ".join(raw.split())
            return collapsed[:80] + ("…" if len(collapsed) > 80 else "")
    s = json.dumps(args)
    return s[:80] + ("…" if len(s) > 80 else "")


def _parse_event(data: dict[str, Any]) -> MessageEvent | None:
    _cleanup_tool_call_names()  # periodic pruning of orphaned entries
    event_type_raw = data.get("type")
    try:
        etype = EventType(event_type_raw)
    except ValueError:
        logger.debug("Unknown event type: %s", event_type_raw)
        return None

    if etype == EventType.PING:
        return MessageEvent(event_type=etype)

    if etype == EventType.ERROR:
        return MessageEvent(event_type=etype, error=data.get("error", "unknown error"))

    if etype == EventType.FINISH:
        ts_raw = data.get("token_state") or {}
        token_state: TokenState | None = None
        if ts_raw:
            token_state = TokenState(
                input_tokens=int(ts_raw.get("inputTokens", 0)),
                output_tokens=int(ts_raw.get("outputTokens", 0)),
                total_tokens=int(ts_raw.get("totalTokens", 0)),
                accumulated_cost=float(ts_raw.get("accumulatedCost", 0.0)),
            )
        return MessageEvent(
            event_type=etype,
            finish_reason=data.get("reason", "stop"),
            token_state=token_state,
        )

    if etype == EventType.MESSAGE:
        msg = data.get("message", {})
        role = msg.get("role", "")
        contents: list[MessageContent] = []
        for item in msg.get("content", []):
            item_type = item.get("type", "")
            if item_type == "text":
                contents.append(MessageContent(content_type="text", text=item.get("text", "")))
            elif item_type == "thinking":
                contents.append(
                    MessageContent(content_type="thinking", thinking=item.get("thinking", ""))
                )
            elif item_type in ("tool_use", "tool_request", "toolRequest"):
                tool_call = item.get("tool_call") or item.get("toolCall") or item
                if isinstance(tool_call, dict) and "Err" in tool_call:
                    tool_call = {}
                # goosed >= 1.35 nests under toolCall.value
                value = tool_call.get("value", {}) if isinstance(tool_call, dict) else {}
                name = value.get("name") or tool_call.get("name") or item.get("name", "")
                args = (
                    value.get("arguments")
                    or value.get("input")
                    or tool_call.get("input")
                    or tool_call.get("arguments", {})
                )
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                preview = _parse_tool_args(args) if args else "{}"
                if not name and not args:
                    name = item.get("tool_name", "") or tool_call.get("tool_name", "") or ""
                # Track tool name by call_id for matching with tool_response
                call_id = item.get("id", "")
                if call_id and name:
                    _tool_call_names[call_id] = name
                contents.append(
                    MessageContent(
                        content_type="tool_request",
                        tool_name=name,
                        args_preview=preview,
                    )
                )
            elif item_type in ("tool_result", "tool_response", "toolResponse"):
                result = item.get("tool_result") or item.get("toolResult") or item
                # Match by call_id: tool_response has an "id" field matching tool_request's id
                call_id = item.get("id", "")
                tool_name = _tool_call_names.pop(call_id, "") if call_id else ""
                if isinstance(result, dict):
                    # goosed >= 1.35 nests under toolResult.value
                    value = result.get("value", {}) if isinstance(result, dict) else {}
                    # Extract text from content array in value, or from top-level Ok
                    content_items = value.get("content", []) or result.get("content", [])
                    ok = result.get("Ok")
                    if ok:
                        # legacy format: {"Ok": [...]}
                        content_items = (
                            ok
                            if isinstance(ok, list)
                            else ok.get("content", [])
                            if isinstance(ok, dict)
                            else []
                        )
                    text = " ".join(
                        c.get("text", "")
                        for c in content_items
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                    # Also extract structuredContent output if available
                    if not text and value.get("structuredContent"):
                        sc = value["structuredContent"]
                        text = sc.get("stdout", "") or sc.get("output", "") or str(sc)[:500]
                    # Check for error in value.isError or top-level Err
                    success = not value.get("isError", False) and "Err" not in result
                else:
                    text = str(result)
                    success = True
                contents.append(
                    MessageContent(
                        content_type="tool_response",
                        tool_name=tool_name,
                        result_preview=text[:500],
                        success=success,
                    )
                )
        return MessageEvent(event_type=etype, role=role, contents=contents)

    return MessageEvent(event_type=etype)


class GoosedClient:
    """Async client for goosed's /reply SSE endpoint."""

    def __init__(
        self,
        base_url: str,
        secret: str = "",
        connect_timeout: float = 10.0,
        read_timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if secret:
            self._headers["x-secret-key"] = secret
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

    async def is_alive(self) -> bool:
        """Quick health check — returns False if goosed is unreachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as c:
                r = await c.get(f"{self._base_url}/status", headers=self._headers)
                return r.is_success
        except Exception:
            return False

    async def ensure_session(self, session_id: str) -> tuple[str, bool]:
        """Ensure a goosed session exists with provider loaded.

        1. Check if session exists → if yes, return (session_id, False).
        2. Create session via POST /agent/start.
        3. Initialize provider via POST /agent/resume {load_model_and_extensions: true}.

        Returns ``(session_id, is_new)`` where *is_new* is True when the session
        was just created (goosed had no history for it).
        """
        async with httpx.AsyncClient(timeout=30.0, verify=False) as c:
            r = await c.get(
                f"{self._base_url}/sessions/{session_id}",
                headers=self._headers,
            )
            if r.status_code == 200:
                # Session exists — still resume so extensions from current
                # config.yaml are loaded (handles goosed restarts with new MCPs).
                r_resume = await c.post(
                    f"{self._base_url}/agent/resume",
                    json={"session_id": session_id, "load_model_and_extensions": True},
                    headers=self._headers,
                )
                if r_resume.is_success:
                    logger.info("Extensions reloaded for existing session %s", session_id)
                else:
                    logger.warning(
                        "agent/resume failed for %s: %s %s",
                        session_id,
                        r_resume.status_code,
                        r_resume.text[:200],
                    )
                return session_id, False  # existing session — history preserved

            # Session doesn't exist — create a new one
            r2 = await c.post(
                f"{self._base_url}/agent/start",
                json={"working_dir": "/tmp"},
                headers=self._headers,
            )
            r2.raise_for_status()
            data = r2.json()
            new_id = data.get("id") or data.get("session_id") or session_id
            logger.info("Created new goosed session: %s", new_id)

            # Initialize provider + extensions (restore_provider_from_session)
            r3 = await c.post(
                f"{self._base_url}/agent/resume",
                json={"session_id": new_id, "load_model_and_extensions": True},
                headers=self._headers,
            )
            r3.raise_for_status()
            logger.info("Provider initialized for session %s", new_id)

            return new_id, True  # new session — context must be injected

    async def reply_stream(
        self,
        session_id: str,
        user_text: str,
    ) -> AsyncIterator[MessageEvent]:
        """POST /reply and yield MessageEvent objects from the SSE stream."""
        import time

        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "created": int(time.time()),
                "content": [{"type": "text", "text": user_text}],
                "metadata": {
                    "userVisible": True,
                    "agentVisible": True,
                },
            },
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=30.0,
                pool=5.0,
            ),
            verify=False,  # goosed uses a self-signed TLS certificate
        ) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/reply",
                json=payload,
                headers=self._headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    logger.info("RAW_SSE: %s", raw[:200])
                    if not raw.strip():
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("Bad SSE JSON: %s", raw[:80])
                        continue
                    event = _parse_event(data)
                    if event is not None:
                        yield event

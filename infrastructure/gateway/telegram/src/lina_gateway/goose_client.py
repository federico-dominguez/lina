"""goosed HTTP/SSE client.

Communicates with the goosed server via its REST API:
  POST /reply  — send a message, receive SSE stream of MessageEvent
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ─── MessageEvent types (mirrors goose-server reply.rs) ─────────────────────

class EventType(str, Enum):
    MESSAGE = "Message"
    FINISH = "Finish"
    ERROR = "Error"
    PING = "Ping"
    NOTIFICATION = "Notification"
    UPDATE_CONVERSATION = "UpdateConversation"
    ACTIVE_REQUESTS = "ActiveRequests"


@dataclass
class MessageContent:
    content_type: str   # "text", "thinking", "tool_request", "tool_response", ...
    text: str = ""
    thinking: str = ""
    tool_name: str = ""
    args_preview: str = ""
    result_preview: str = ""
    success: bool | None = None


@dataclass
class MessageEvent:
    event_type: EventType
    # For Message events
    role: str = ""
    contents: list[MessageContent] = field(default_factory=list)
    # For Finish events
    finish_reason: str = ""
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
        return MessageEvent(event_type=etype, finish_reason=data.get("reason", "stop"))

    if etype == EventType.MESSAGE:
        msg = data.get("message", {})
        role = msg.get("role", "")
        contents: list[MessageContent] = []
        for item in msg.get("content", []):
            item_type = item.get("type", "")
            if item_type == "text":
                contents.append(MessageContent(content_type="text", text=item.get("text", "")))
            elif item_type == "thinking":
                contents.append(MessageContent(content_type="thinking", thinking=item.get("thinking", "")))
            elif item_type == "tool_use" or item_type == "tool_request":
                tool_call = item.get("tool_call") or item
                if isinstance(tool_call, dict) and "Err" in tool_call:
                    tool_call = {}
                name = tool_call.get("name", item.get("name", ""))
                args = tool_call.get("input", tool_call.get("arguments", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                preview = _parse_tool_args(args)
                contents.append(MessageContent(
                    content_type="tool_request",
                    tool_name=name,
                    args_preview=preview,
                ))
            elif item_type == "tool_result" or item_type == "tool_response":
                result = item.get("tool_result") or item
                if isinstance(result, dict):
                    ok = result.get("Ok") or result
                    items = ok if isinstance(ok, list) else ok.get("content", []) if isinstance(ok, dict) else []
                    text = " ".join(
                        c.get("text", "") for c in items if isinstance(c, dict) and c.get("type") == "text"
                    )
                    success = "Err" not in result
                else:
                    text = str(result)
                    success = True
                contents.append(MessageContent(
                    content_type="tool_response",
                    result_preview=text[:500],
                    success=success,
                ))
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
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self._base_url}/health")
                return r.is_success
        except Exception:
            return False

    async def reply_stream(
        self,
        session_id: str,
        user_text: str,
    ) -> AsyncIterator[MessageEvent]:
        """POST /reply and yield MessageEvent objects from the SSE stream."""
        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "content": [{"type": "text", "text": user_text}],
            },
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=30.0,
                pool=5.0,
            )
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

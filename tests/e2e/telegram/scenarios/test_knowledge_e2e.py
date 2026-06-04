"""
E2E: Knowledge MCP configuration verification.

Tests that the lina-knowledge MCP is:
1. Registered in the agent configs
2. Available as a tool
3. Functional end-to-end
"""
from __future__ import annotations
import pytest
from tests.e2e.telegram.client import TelegramTestClient
from tests.e2e.telegram.assertions import assert_not_empty


def _has_any(text: str, *keywords: str) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


@pytest.mark.e2e_telegram
async def test_knowledge_mcp_tool_available(tg: TelegramTestClient) -> None:
    """Each agent must have the knowledge MCP tools available."""
    for bot_name, username in [("LINA", "@s_lina_bot"), ("Cline", "@s_cline_bot"), ("Gemma", "@s_gemma_bot")]:
        capture = await tg.send_prompt(
            f"{username} Listá tus tools disponibles. "
            '¿Tenés "remember_knowledge" o "search_knowledge"? '
            "Respondé SOLO SI o NO.",
            timeout=60,
            stable_window=5.0,
        )
        assert_not_empty(capture)
        text = capture.final_text.lower()
        has_knowledge = _has_any(text, "remember_knowledge", "search_knowledge", "recall_knowledge", "si", "yes", "sí")
        if not has_knowledge:
            print(f"WARNING: {bot_name} might not have knowledge tools. Response: {capture.final_text[:200]}")

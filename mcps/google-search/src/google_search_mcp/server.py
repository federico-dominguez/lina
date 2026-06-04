#!/usr/bin/env python3
"""Google Search MCP — Google Search grounding via Gemini API.
    
Uso: GOOGLE_API_KEY=... google-search-mcp
"""

import os, json, sys
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("google-search")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

API_BASE = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _search(query: str) -> str:
    if not GOOGLE_API_KEY:
        return "Error: GOOGLE_API_KEY no configurada"
    url = f"{API_BASE}?alt=sse&key={GOOGLE_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3},
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error HTTP: {e}"
    texts = []
    for line in r.text.split("\n"):
        if line.startswith("data: ") and line.strip() != "data: ":
            try:
                data = json.loads(line[6:])
                for c in data.get("candidates", []):
                    for p in c.get("content", {}).get("parts", []):
                        if "text" in p:
                            texts.append(p["text"])
            except json.JSONDecodeError:
                pass
    return "\n".join(texts) if texts else "(sin resultados)"


@mcp.tool()
def google_search(query: str) -> str:
    """Busca en internet usando Google Search nativo (Gemini grounding).
    Args:
        query: Consulta en lenguaje natural
    Returns:
        Resultados actualizados con citas de Google
    """
    return _search(query)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

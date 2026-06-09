"""
E2E: Pipeline flow simulation via Telegram Comm group.

Tests the COMPLETE user experience:
1. @mentions to each bot in the Comm group
2. Response times (TTFT, TTLT)
3. Cross-agent communication
4. Pipeline step completion within timeouts
"""

from __future__ import annotations

import asyncio
import re
import pytest

from tests.e2e.telegram.client import TelegramTestClient
from tests.e2e.telegram.assertions import (
    assert_not_empty,
    assert_ttft_under,
    assert_ttlt_under,
)

BOTS = {
    "LINA": {"username": "@s_lina_bot", "name": "LINA"},
    "Cline": {"username": "@s_cline_bot", "name": "Cline"},
    "Gemma": {"username": "@s_gemma_bot", "name": "Gemma"},
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_any(text: str, *keywords: str) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)

def _has_all(text: str, *keywords: str) -> bool:
    lowered = text.lower()
    return all(k.lower() in lowered for k in keywords)

MARKERS = {
    "🔍": "analyze",
    "🔬": "research",
    "🛠️": "dev",
    "🔎": "review",
    "🧪": "test",
    "📋": "analysis",
    "📄": "doc",
}

# ── Test 1: LINA Analyze step ──────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_lina_analyze_timeout(tg: TelegramTestClient) -> None:
    """LINA must respond to an analyze prompt within pipeline timeout (900s).

    Pipeline equivalent: Analyze step (LINA).
    Acceptance: TTFT < 30s, TTLT < 180s, response contains plan/analysis.
    """
    capture = await tg.send_prompt(
        "@s_lina_bot Revisá el issue #152 en github.com/federico-dominguez/lina/issues/152. "
        "Tu rol: TECH LEAD. Creá un plan de implementación para el sistema de memoria unificada. "
        "Respondé con un plan paso a paso.",
        timeout=300,
        stable_window=15.0,
    )
    assert_not_empty(capture)
    assert_ttft_under(capture, seconds=30.0)
    assert_ttlt_under(capture, seconds=180.0)
    text = capture.final_text.lower()
    assert _has_any(text, "plan", "paso", "fase", "implementar", "arquitectura"), (
        f"LINA no generó un plan de implementación. Resp: {capture.final_text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_lina_analyze_tokens_under_limit(tg: TelegramTestClient) -> None:
    """LINA's analyze response should be concise (< 10k chars).

    Pipeline timeout: 900s. Long responses delay the pipeline.
    """
    capture = await tg.send_prompt(
        "@s_lina_bot Analizá el issue #153 (pipeline improvements). "
        "Dame solo los puntos clave, máximo 500 palabras.",
        timeout=180,
        stable_window=10.0,
    )
    assert_not_empty(capture)
    total_chars = sum(len(m.final_text) for m in capture.final_messages)
    assert total_chars < 10000, (
        f"Analyze response too long: {total_chars} chars. "
        f"Pipeline timeout is 900s but long responses waste tokens. "
        f"Response: {capture.final_text[:200]}..."
    )


# ── Test 2: Cline Dev step ──────────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_cline_dev_creates_branch(tg: TelegramTestClient) -> None:
    """Cline must create a proper branch with conventional naming.

    Pipeline equivalent: Dev step (Cline).
    Acceptance: response contains a branch name matching feat/fix/refactor/issue-<N>.
    """
    capture = await tg.send_prompt(
        "@s_cline_bot Revisá el issue #153. Creá un branch y respondeme "
        "SOLO con el nombre del branch que creaste.",
        timeout=300,
        stable_window=20.0,
    )
    assert_not_empty(capture)
    text = capture.final_text
    assert re.search(r'(feat|fix|refactor|docs|chore)/[w-]+', text), (
        f"Cline no creó un branch con naming convencional. Resp: {text[:300]}"
    )


@pytest.mark.e2e_telegram
async def test_cline_dev_runs_tests_before_push(tg: TelegramTestClient) -> None:
    """Cline must run tests before pushing code.

    Pipeline equivalent: Dev step "tests before push" rule.
    Acceptance: response mentions tests passing or pytest output.
    """
    capture = await tg.send_prompt(
        "@s_cline_bot Implementá un cambio pequeño en el pipeline: agregá "
        "logging al inicio de run_pipeline. Corré los tests y decime el resultado.",
        timeout=600,
        stable_window=30.0,
    )
    assert_not_empty(capture)
    text = capture.final_text.lower()
    # Cline must mention test results
    assert _has_any(text, "test", "pytest", "passed", "failed", "ok", "exit code"), (
        f"Cline no mencionó test results. Resp: {capture.final_text[:300]}"
    )


# ── Test 3: Gemma Review step ──────────────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_gemma_review_approves_or_rejects(tg: TelegramTestClient) -> None:
    """Gemma must review code and approve/reject with reason.

    Pipeline equivalent: Review step (Gemma).
    Acceptance: response contains ✅ or ❌ with a reason.
    """
    capture = await tg.send_prompt(
        "@s_gemma_bot Revisá el branch feat/issue-153-pipeline-improvements. "
        "Revisá el diff con main: git diff main...HEAD. "
        "Decime si aprobás o rechazás y por qué.",
        timeout=300,
        stable_window=15.0,
    )
    assert_not_empty(capture)
    text = capture.final_text
    has_approve = "✅" in text or "APROB" in text.upper()
    has_reject = "❌" in text or "RECHAZ" in text.upper() or "REJECT" in text.upper()
    assert has_approve or has_reject, (
        f"Gemma no aprobó ni rechazó. Resp: {text[:400]}"
    )
    assert len(text) > 100, (
        f"Gemma no dio razón. Resp muy corta: {text[:200]}"
    )


# ── Test 4: Knowledge MCP via Telegram ──────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_knowledge_store_and_search(tg: TelegramTestClient) -> None:
    """Store and search knowledge via Telegram end-to-end.

    Tests the full knowledge MCP pipeline:
    1. User sends @mention to LINA to store knowledge
    2. LINA uses remember_knowledge tool
    3. User asks about the same topic
    4. LINA uses search_knowledge and retrieves it
    """
    import time

    # Step 1: Store knowledge
    capture1 = await tg.send_prompt(
        "@s_lina_bot Usá remember_knowledge para guardar: "
        "'El deploy de lina-knowledge requiere agregar el MCP al config de cada agente "
        "y reconstruir las imágenes Docker'. "
        "Confirmame que se guardó.",
        timeout=120,
        stable_window=10.0,
    )
    assert_not_empty(capture1)
    text1 = capture1.final_text.lower()
    assert _has_any(text1, "guard", "ok", "listo", "correct", "éxito"), (
        f"LINA no confirmó guardado. Resp: {capture1.final_text[:300]}"
    )

    await asyncio.sleep(2)  # Wait for DB persistence

    # Step 2: Search knowledge (with different wording to test semantic search)
    capture2 = await tg.send_prompt(
        "@s_lina_bot Usá search_knowledge para buscar: "
        "'cómo desplegar el nuevo MCP de memoria'. "
        "Mostrame los resultados.",
        timeout=120,
        stable_window=10.0,
    )
    assert_not_empty(capture2)
    text2 = capture2.final_text.lower()
    assert _has_any(text2, "lina-knowledge", "mcp", "deploy", "docker", "imagen", "agente", "config"), (
        f"LINA no recuperó la knowledge. Resp: {capture2.final_text[:400]}"
    )
    assert not _has_any(text2, "no encontré", "sin resultados", "0 resultados", "error"), (
        f"LINA reportó error en búsqueda. Resp: {capture2.final_text[:400]}"
    )


# ── Test 5: Cross-agent knowledge sharing ───────────────────────────────────

@pytest.mark.e2e_telegram
async def test_knowledge_shared_across_agents(tg: TelegramTestClient) -> None:
    """Knowledge stored by one agent must be retrievable by another.

    Uses the same Telethon session (same Telegram user) to:
    1. Ask Gemma to search knowledge about deploy
    2. Verify Gemma can find the knowledge stored by LINA
    """
    import time

    # First ensure knowledge exists (store via LINA)
    capture0 = await tg.send_prompt(
        "@s_lina_bot Usá remember_knowledge para guardar: "
        "'El servidor MCP lina-knowledge corre en puerto 8090 y "
        "usa pgvector para búsqueda semántica'. Confirmame.",
        timeout=60,
        stable_window=5.0,
    )
    assert_not_empty(capture0)
    await asyncio.sleep(2)

    # Now ask Gemma (different agent!) to find it
    capture = await tg.send_prompt(
        "@s_gemma_bot Usá search_knowledge para buscar: "
        "'qué puerto usa el MCP de conocimiento'. Mostrame el resultado.",
        timeout=120,
        stable_window=10.0,
    )
    assert_not_empty(capture)
    text = capture.final_text.lower()
    assert _has_any(text, "8090", "lina-knowledge", "pgvector", "mcp"), (
        f"Gemma no encontró knowledge almacenada por LINA. Resp: {capture.final_text[:400]}"
    )


# ── Test 6: Pipeline end-to-end latency ─────────────────────────────────────

@pytest.mark.e2e_telegram
async def test_pipeline_e2e_total_time(tg: TelegramTestClient) -> None:
    """Simulate a full pipeline run and measure total latency.

    Sends @mentions to each bot sequentially (like the pipeline does)
    and measures the total round-trip time.

    Acceptance: Total pipeline time < 30 min for a simple task.
    """
    import time

    t0 = time.monotonic()
    tasks = []

    # Step 1: LINA analyze
    t1 = time.monotonic()
    c1 = await tg.send_prompt(
        "@s_lina_bot Analizá rápido: ¿qué archivos necesito modificar "
        "para agregar un MCP 'hello-world'? Respondé en 1 línea.",
        timeout=120,
        stable_window=5.0,
    )
    t_analyze = time.monotonic() - t1
    assert_not_empty(c1)

    # Step 2: Cline dev
    t2 = time.monotonic()
    c2 = await tg.send_prompt(
        "@s_cline_bot Creá un archivo /tmp/hello-world.txt con "
        "el texto 'pipeline e2e test'. Decime cuando esté listo.",
        timeout=120,
        stable_window=5.0,
    )
    t_dev = time.monotonic() - t2
    assert_not_empty(c2)

    # Step 3: Gemma test
    t3 = time.monotonic()
    c3 = await tg.send_prompt(
        "@s_gemma_bot Verificá rápidamente que el archivo "
        "/tmp/hello-world.txt existe y tiene contenido. Respondé SI o NO.",
        timeout=120,
        stable_window=5.0,
    )
    t_test = time.monotonic() - t3

    total = time.monotonic() - t0

    # Report metrics
    report = (
        f"PIPELINE E2E METRICS:\n"
        f"  Analyze: {t_analyze:.1f}s ({len(c1.final_text)} chars)\n"
        f"  Dev:     {t_dev:.1f}s ({len(c2.final_text)} chars)\n"
        f"  Test:    {t_test:.1f}s ({len(c3.final_text)} chars)\n"
        f"  Total:   {total:.1f}s (pipeline timeout: 900s)\n"
    )
    print(f"\n{report}")

    # Assert total pipeline time < 15 min (simple task)
    assert total < 900, f"Pipeline total time too high: {total:.0f}s > 900s"

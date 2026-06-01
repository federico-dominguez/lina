"""Prototipo Fase A — Manager + 2 workers con mcp-agent.

Este prototipo es independiente del runtime de producción. Demuestra que el
patrón Orchestrator-Workers de Anthropic se puede ejecutar sobre mcp-agent
con un manager LLM que delega a workers especializados (`dev`, `study`) en
paralelo y consolida resultados.

Acceptance del prototipo:
1. Spawn 2 workers en paralelo con MCPs filtrados por rol.
2. Manager recibe un goal compuesto y arma plan.
3. Cada worker ejecuta su sub-tarea y devuelve resultado.
4. Manager consolida y devuelve respuesta única.
5. Latencia, tokens y peso de la imagen son medidos.

NO es código de producción. Se borra cuando Fase B aterrice el orquestador
real en `mcps/orchestrator/`.

Uso:
    uv run --with 'mcp-agent[openai]' python application/orchestration/prototype.py
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

# ─── Especificaciones declarativas de los workers ────────────────────────────
# Esto se moverá a config/agents.yaml en Fase B.


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    role: str
    instruction: str
    server_names: tuple[str, ...]


WORKERS: dict[str, WorkerSpec] = {
    "dev": WorkerSpec(
        name="dev-worker",
        role="dev",
        instruction=(
            "Sos un agente desarrollador. Tu rol es analizar código, listar "
            "archivos y proponer cambios. Tenés acceso solo al filesystem del "
            "repo. No podés ejecutar comandos shell ni hacer cambios en GitHub. "
            "Respondé en 2-3 frases."
        ),
        server_names=("filesystem",),
    ),
    "study": WorkerSpec(
        name="study-worker",
        role="study",
        instruction=(
            "Sos un agente de estudio. Tu rol es buscar información en la web "
            "y resumirla. Tenés acceso solo a fetch HTTP. No podés tocar el "
            "filesystem ni ejecutar nada. Respondé en 2-3 frases."
        ),
        server_names=("fetch",),
    ),
}


# ─── Orquestación ────────────────────────────────────────────────────────────


async def run_worker(spec: WorkerSpec, subtask: str) -> tuple[str, str, float, int]:
    """Lanza un worker, ejecuta una sub-tarea y devuelve (role, output, secs, tokens)."""
    # Imports tardíos para que el archivo se pueda leer sin deps instaladas.
    from mcp_agent.agents.agent import Agent
    from mcp_agent.app import MCPApp
    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

    app = MCPApp(name=f"proto-{spec.role}")
    started = time.monotonic()

    async with app.run() as running:
        agent = Agent(
            name=spec.name,
            instruction=spec.instruction,
            server_names=list(spec.server_names),
        )
        async with agent:
            llm = await agent.attach_llm(OpenAIAugmentedLLM)
            output = await llm.generate_str(message=subtask)

        # TokenCounter está expuesto en el contexto cuando tracing está activo.
        # Si no, devolvemos 0 (no rompe el prototipo).
        tokens = 0
        counter = getattr(running.context, "token_counter", None)
        if counter is not None:
            try:
                usage = await counter.snapshot()  # type: ignore[attr-defined]
                tokens = getattr(usage, "total_tokens", 0)
            except Exception:
                tokens = 0

    return spec.role, output, time.monotonic() - started, tokens


async def manager_orchestrate(goal: str, plan: dict[str, str]) -> str:
    """Manager simplificado: ejecuta el plan en paralelo y consolida.

    En producción (Fase B), el plan lo genera el LLM manager vía
    `create_orchestrator()` de mcp-agent. Acá lo recibimos hardcodeado para
    aislar la prueba del patrón.
    """
    print(f"\n[manager] goal recibido: {goal}")
    print(f"[manager] plan: {list(plan.keys())} en paralelo\n")

    tasks = [
        run_worker(WORKERS[role], subtask) for role, subtask in plan.items() if role in WORKERS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    consolidated: list[str] = [f"Respuesta consolidada para: {goal}", ""]
    total_secs = 0.0
    total_tokens = 0

    for item in results:
        if isinstance(item, BaseException):
            consolidated.append(f"❌ worker falló: {item!r}")
            continue
        role, output, secs, tokens = item
        consolidated.append(f"• [{role}] ({secs:.1f}s, {tokens} tok)\n  {output.strip()}")
        total_secs = max(total_secs, secs)  # paralelo: nos quedamos con el más lento
        total_tokens += tokens

    consolidated.append("")
    consolidated.append(f"⏱️  paralelo: {total_secs:.1f}s · 🔢 tokens: {total_tokens}")
    return "\n".join(consolidated)


# ─── Entry point ─────────────────────────────────────────────────────────────


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY") and not os.path.exists("mcp_agent.secrets.yaml"):
        print("⚠️  Falta OPENAI_API_KEY o mcp_agent.secrets.yaml — el prototipo no puede correr.")
        print("    Esto es esperado en CI. El prototipo se valida manualmente.")
        return

    goal = "Listame los archivos del repo Y resumime de qué trata MCP."
    plan = {
        "dev": "Listá los archivos en el directorio actual (top-level) y decime cuántos hay.",
        "study": "Fetcheá https://modelcontextprotocol.io/ y resumime en 2 frases qué es MCP.",
    }
    answer = await manager_orchestrate(goal, plan)
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())

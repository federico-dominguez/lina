"""cost_guard.py — Cost tracking y rate-limiting para sub-agentes (issue #91).

Flujo:
    post_finish_handler(session_id, role, model, token_counts)
        → calcula costo USD con calculate_token_cost()
        → INSERT en cost_ledger
    
    check_spawn_budget(role, max_usd_per_day)
        → SELECT SUM(usd_cost) FROM cost_ledger WHERE role=? AND TODAY
        → retorna (allowed, current_usd, max_usd)

DeepSeek V4 pricing (junio 2026, oficial):
    deepseek-v4-flash: input cache hit=$0.0028/1M, miss=$0.14/1M, output=$0.28/1M
    deepseek-v4-pro:   input cache hit=$0.003625/1M, miss=$0.435/1M, output=$0.87/1M
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import NamedTuple

log = logging.getLogger("lina-orchestrator.cost")

# ─── Pricing model ─────────────────────────────────────────────────────────────
# Precios por token (USD). Ej: $0.14/1M tokens → 0.14 / 1_000_000 = 1.4e-7

DEEPSEEK_PRICING: dict[str, dict[str, Decimal]] = {
    "deepseek-v4-flash": {
        "input_cache_hit":  Decimal("0.0000000028"),  # $0.0028/1M
        "input_cache_miss": Decimal("0.00000014"),    # $0.14/1M
        "output":           Decimal("0.00000028"),    # $0.28/1M
    },
    "deepseek-v4-pro": {
        "input_cache_hit":  Decimal("0.000000003625"), # $0.003625/1M
        "input_cache_miss": Decimal("0.000000435"),    # $0.435/1M
        "output":           Decimal("0.00000087"),     # $0.87/1M
    },
}

_DEFAULT_PRICING = DEEPSEEK_PRICING["deepseek-v4-flash"]


class TokenCounts(NamedTuple):
    """Token counts provenientes del evento Finish de goosed."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


class BudgetResult(NamedTuple):
    """Resultado de verificación de presupuesto."""
    allowed: bool
    current_usd: Decimal
    max_usd: Decimal
    reason: str = ""


# ─── Cálculo de costos ─────────────────────────────────────────────────────────


def calculate_token_cost(
    model: str = "deepseek-v4-flash",
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    completion_tokens: int = 0,
) -> Decimal:
    """Calcula costo USD desde token counts usando precios DeepSeek V4.

    Args:
        model: nombre del modelo.
        cache_hit_tokens: tokens servidos desde cache.
        cache_miss_tokens: tokens que requirieron inferencia completa.
        completion_tokens: tokens de output generados.

    Returns:
        Costo total en USD.
    """
    pricing = DEEPSEEK_PRICING.get(model, _DEFAULT_PRICING)

    cost = (
        Decimal(str(cache_hit_tokens)) * pricing["input_cache_hit"]
        + Decimal(str(cache_miss_tokens)) * pricing["input_cache_miss"]
        + Decimal(str(completion_tokens)) * pricing["output"]
    )
    return cost


# ─── Persistencia en cost_ledger ───────────────────────────────────────────────


def write_cost_ledger(
    session_id: str,
    role: str,
    model: str,
    token_counts: TokenCounts,
    usd_cost: Decimal,
) -> None:
    """Escribe un registro en cost_ledger tras la finalización de un sub-agente.

    Args:
        session_id: UUID del sub-agente.
        role: rol del sub-agente (dev/ops/study/etc).
        model: modelo usado.
        token_counts: TokenCounts con prompt/completion/cache.
        usd_cost: costo calculado en USD.
    """
    import psycopg2

    from .spawner import _db_conn  # noqa: PLC0415

    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cost_ledger
                   (session_id, role, model, prompt_tokens, completion_tokens,
                    cache_hit_tokens, cache_miss_tokens, usd_cost)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    session_id,
                    role,
                    model,
                    token_counts.prompt_tokens,
                    token_counts.completion_tokens,
                    token_counts.cache_hit_tokens,
                    token_counts.cache_miss_tokens,
                    float(usd_cost),
                ),
            )
        conn.commit()
        log.info(
            "cost_ledger: session=%s role=%s cost=%.6f",
            session_id, role, float(usd_cost),
        )
    finally:
        conn.close()


# ─── Verificación de presupuesto ────────────────────────────────────────────────


def check_daily_budget(role: str, max_usd_per_day: Decimal | float) -> BudgetResult:
    """Verifica si el rol excedió su presupuesto diario (USD).

    Args:
        role: nombre del rol.
        max_usd_per_day: presupuesto máximo diario (0 = sin límite).

    Returns:
        BudgetResult con allowed, current_usd, max_usd, reason.
    """
    if max_usd_per_day == 0:
        return BudgetResult(allowed=True, current_usd=Decimal("0"), max_usd=Decimal("0"))

    import psycopg2

    from .spawner import _db_conn  # noqa: PLC0415

    max_usd = Decimal(str(max_usd_per_day))
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COALESCE(SUM(usd_cost), 0)::NUMERIC(12, 8)
                   FROM cost_ledger
                   WHERE role = %s
                     AND recorded_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
                     AND recorded_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day'""",
                (role,),
            )
            row = cur.fetchone()
            current_usd = Decimal(str(row[0])) if row else Decimal("0")
    finally:
        conn.close()

    if current_usd > max_usd:
        return BudgetResult(
            allowed=False,
            current_usd=current_usd,
            max_usd=max_usd,
            reason=(
                f"Rol '{role}' excedió presupuesto diario: "
                f"${float(current_usd):.4f} usado de ${float(max_usd):.2f} permitidos"
            ),
        )

    return BudgetResult(
        allowed=True,
        current_usd=current_usd,
        max_usd=max_usd,
    )


# ─── Handler post-Finish ────────────────────────────────────────────────────────


def post_finish_handler(
    session_id: str,
    role: str,
    model: str = "deepseek-v4-flash",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> Decimal:
    """Procesa el evento Finish de un sub-agente: calcula costo y escribe en ledger.

    Args:
        session_id: UUID del sub-agente.
        role: rol del sub-agente.
        model: modelo usado.
        prompt_tokens: total de tokens de input.
        completion_tokens: tokens de output.
        cache_hit_tokens: tokens de input servidos desde cache.
        cache_miss_tokens: tokens de input que requirieron inferencia.

    Returns:
        Costo en USD registrado.
    """
    tokens = TokenCounts(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens or prompt_tokens,
        cache_miss_tokens=cache_miss_tokens or 0,
    )

    usd_cost = calculate_token_cost(
        model=model,
        cache_hit_tokens=tokens.cache_hit_tokens,
        cache_miss_tokens=tokens.cache_miss_tokens,
        completion_tokens=tokens.completion_tokens,
    )

    write_cost_ledger(
        session_id=session_id,
        role=role,
        model=model,
        token_counts=tokens,
        usd_cost=usd_cost,
    )

    return usd_cost

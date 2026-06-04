"""Tests para cost_guard: cálculo de costos (issue #91).

Usa import directo del módulo fuente vía sys.path.
"""

import sys
from decimal import Decimal
from pathlib import Path

# Agregar el source del orchestrator al path
_SRC = str(Path(__file__).resolve().parents[3] / "mcps" / "orchestrator" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lina_orchestrator.infrastructure.cost_guard import (  # noqa: E402
    BudgetResult,
    TokenCounts,
    calculate_token_cost,
)


class TestTokenCost:
    """Pruebas unitarias de calculate_token_cost."""

    def test_flash_cache_hit_only(self):
        cost = calculate_token_cost(
            model="deepseek-v4-flash",
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=0,
            completion_tokens=0,
        )
        assert cost == Decimal("0.00280000"), f"Expected 0.0028, got {cost}"

    def test_flash_cache_miss_only(self):
        cost = calculate_token_cost(
            model="deepseek-v4-flash",
            cache_hit_tokens=0,
            cache_miss_tokens=1_000_000,
            completion_tokens=0,
        )
        assert cost == Decimal("0.14000000"), f"Expected 0.14, got {cost}"

    def test_flash_output_only(self):
        cost = calculate_token_cost(
            model="deepseek-v4-flash",
            cache_hit_tokens=0,
            cache_miss_tokens=0,
            completion_tokens=1_000_000,
        )
        assert cost == Decimal("0.28000000"), f"Expected 0.28, got {cost}"

    def test_pro_mixed(self):
        cost = calculate_token_cost(
            model="deepseek-v4-pro",
            cache_hit_tokens=500_000,
            cache_miss_tokens=200_000,
            completion_tokens=100_000,
        )
        expected = Decimal("0.00181250") + Decimal("0.08700000") + Decimal("0.08700000")
        assert cost == expected, f"Expected {expected}, got {cost}"

    def test_zero_tokens(self):
        cost = calculate_token_cost(
            model="deepseek-v4-flash",
            cache_hit_tokens=0,
            cache_miss_tokens=0,
            completion_tokens=0,
        )
        assert cost == Decimal("0"), f"Expected 0, got {cost}"

    def test_unknown_model_fallback(self):
        cost = calculate_token_cost(
            model="unknown-model",
            cache_hit_tokens=1_000_000,
            cache_miss_tokens=0,
            completion_tokens=0,
        )
        assert cost == Decimal("0.00280000"), f"Expected flash pricing fallback, got {cost}"


class TestTokenCounts:
    """Pruebas de TokenCounts NamedTuple."""

    def test_defaults_zero(self):
        t = TokenCounts()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0
        assert t.cache_hit_tokens == 0
        assert t.cache_miss_tokens == 0

    def test_mixed_values(self):
        t = TokenCounts(
            prompt_tokens=5000,
            completion_tokens=1200,
            cache_hit_tokens=4000,
            cache_miss_tokens=1000,
        )
        assert t.prompt_tokens == 5000
        assert t.completion_tokens == 1200
        assert t.cache_hit_tokens == 4000


class TestBudgetResult:
    """Pruebas de BudgetResult."""

    def test_allowed(self):
        r = BudgetResult(allowed=True, current_usd=Decimal("1.0"), max_usd=Decimal("10.0"))
        assert r.allowed is True
        assert r.current_usd == Decimal("1.0")
        assert r.max_usd == Decimal("10.0")

    def test_exceeded_with_reason(self):
        r = BudgetResult(
            allowed=False,
            current_usd=Decimal("15.0"),
            max_usd=Decimal("10.0"),
            reason="Rol 'dev' excedió presupuesto diario",
        )
        assert r.allowed is False
        assert "excedió" in r.reason

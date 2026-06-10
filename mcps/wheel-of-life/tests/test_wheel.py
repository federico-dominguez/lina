"""Tests for Wheel of Life MCP server and store."""

from __future__ import annotations

import os
import pytest

os.environ.setdefault("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")

from lina_wheel import store


def test_areas():
    assert len(store.AREAS) == 8
    assert store.AREAS[0] == "salud"
    assert store.AREAS[-1] == "espiritualidad"


@pytest.mark.db
def test_save_and_retrieve():
    store.save_evaluation("salud", 8, "Me siento con energía")
    wheel = store.get_current_wheel()
    areas = {r["area"]: r for r in wheel}
    assert "salud" in areas
    assert areas["salud"]["score"] == 8
    assert areas["salud"]["notes"] == "Me siento con energía"


@pytest.mark.db
def test_batch_save():
    evals = [
        {"area": "trabajo", "score": 7, "notes": "Buen desafío"},
        {"area": "amor", "score": 9, "notes": ""},
    ]
    # Save via store directly
    for ev in evals:
        store.save_evaluation(ev["area"], ev["score"], ev.get("notes", ""))
    wheel = store.get_current_wheel()
    areas = {r["area"]: r for r in wheel}
    assert areas.get("trabajo", {}).get("score") == 7
    assert areas.get("amor", {}).get("score") == 9


def test_invalid_score():
    try:
        store.save_evaluation("salud", 0)
        assert False, "Debería haber lanzado ValueError"
    except ValueError:
        pass


def test_invalid_area():
    try:
        store.save_evaluation("nonexistente", 5)
        assert False, "Debería haber lanzado ValueError"
    except ValueError:
        pass


@pytest.mark.db
def test_history():
    result = store.get_wheel_history(2)
    assert isinstance(result, list)
    if result:
        week = result[0]
        assert "week_start" in week
        assert "areas" in week
        assert "average" in week


@pytest.mark.db
def test_wheel_current_structure():
    from lina_wheel.store import AREAS

    # Ensure at least one area has data
    if not store.get_current_wheel():
        store.save_evaluation("salud", 6)
    result = store.get_current_wheel()
    assert isinstance(result, list)
    for r in result:
        assert "area" in r
        assert "score" in r
        assert "evaluated_at" in r
        assert r["area"] in AREAS

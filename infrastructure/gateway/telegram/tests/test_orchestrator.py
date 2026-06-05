"""Tests for the orchestration module (IntentClassifier, CapabilityRegistry, ConversationRouter)."""

import pytest

from lina_gateway.orchestrator import (
    BotCapability,
    CapabilityRegistry,
    ConversationRouter,
    Intent,
    IntentCategory,
    IntentClassifier,
    RouteDecision,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the CapabilityRegistry singleton before each test."""
    CapabilityRegistry.reset()
    yield


@pytest.fixture
def classifier() -> IntentClassifier:
    return IntentClassifier()


@pytest.fixture
def router() -> ConversationRouter:
    return ConversationRouter()


# ─── IntentClassifier Tests ──────────────────────────────────────────────────


class TestIntentClassifier:
    """Pruebas unitarias de IntentClassifier."""

    def test_classify_dev(self, classifier: IntentClassifier):
        intent = classifier.classify("implementá un endpoint /health")
        assert intent.category == IntentCategory.DEV
        assert intent.confidence >= 0.55
        assert len(intent.matched_keywords) > 0

    def test_classify_study(self, classifier: IntentClassifier):
        intent = classifier.classify("estudiá el curso de moodle")
        assert intent.category == IntentCategory.STUDY
        assert intent.confidence >= 0.55
        assert len(intent.matched_keywords) > 0

    def test_classify_research(self, classifier: IntentClassifier):
        intent = classifier.classify("investigá qué es MCP")
        assert intent.category == IntentCategory.RESEARCH
        assert intent.confidence >= 0.55
        assert len(intent.matched_keywords) > 0

    def test_classify_ops(self, classifier: IntentClassifier):
        intent = classifier.classify("reiniciá el contenedor docker")
        assert intent.category == IntentCategory.OPS
        assert intent.confidence >= 0.55
        assert len(intent.matched_keywords) > 0

    def test_classify_general(self, classifier: IntentClassifier):
        intent = classifier.classify("hola cómo estás")
        assert intent.category == IntentCategory.GENERAL
        assert intent.confidence >= 0.5

    def test_classify_command(self, classifier: IntentClassifier):
        intent = classifier.classify("/status")
        assert intent.category == IntentCategory.COMMAND
        assert intent.confidence == 1.0

    def test_classify_short(self, classifier: IntentClassifier):
        intent = classifier.classify("ok")
        assert intent.category == IntentCategory.GENERAL
        assert intent.confidence >= 0.5

    def test_classify_empty(self, classifier: IntentClassifier):
        intent = classifier.classify("")
        assert intent.category == IntentCategory.GENERAL

    def test_classify_none_text(self, classifier: IntentClassifier):
        intent = classifier.classify("   ")
        assert intent.category == IntentCategory.GENERAL


# ─── ConversationRouter Tests ────────────────────────────────────────────────


class TestConversationRouter:
    """Pruebas unitarias de ConversationRouter."""

    def test_route_dev_to_cline(self, router: ConversationRouter):
        """'implementá endpoint' desde LINA → cline"""
        decision = router.route("implementá un endpoint", current_bot="lina")
        assert decision.should_route is True
        assert decision.target_bot == "cline"
        assert decision.target_username == "s_cline_bot"
        assert decision.intent_category == "dev"

    def test_route_study_to_lina(self, router: ConversationRouter):
        """'estudiá moodle' desde Cline → lina"""
        decision = router.route("estudiá el curso de moodle", current_bot="cline")
        assert decision.should_route is True
        assert decision.target_bot == "lina"
        assert decision.target_username == "s_lina_bot"
        assert decision.intent_category == "study"

    def test_route_ops_to_goose(self, router: ConversationRouter):
        """'docker compose' desde LINA → goose"""
        decision = router.route("docker compose up", current_bot="lina")
        assert decision.should_route is True
        assert decision.target_bot == "goose"
        assert decision.target_username == "s_goose_bot"
        assert decision.intent_category == "ops"

    def test_route_general_stays(self, router: ConversationRouter):
        """'hola' → no route"""
        decision = router.route("hola", current_bot="lina")
        assert decision.should_route is False

    def test_route_command_stays(self, router: ConversationRouter):
        """'/status' → no route"""
        decision = router.route("/status", current_bot="lina")
        assert decision.should_route is False

    def test_route_empty_text(self, router: ConversationRouter):
        decision = router.route("", current_bot="lina")
        assert decision.should_route is False

    def test_route_unknown_bot(self, router: ConversationRouter):
        """Bot desconocido que no soporta dev → redirige al mejor."""
        decision = router.route("implementá algo", current_bot="unknown")
        assert decision.should_route is True


# ─── CapabilityRegistry Tests ────────────────────────────────────────────────


class TestCapabilityRegistry:
    """Pruebas unitarias de CapabilityRegistry."""

    def test_lina_capabilities(self):
        registry = CapabilityRegistry()
        cap = registry.get_capability("lina")
        assert cap is not None
        assert cap.name == "lina"
        assert cap.username == "s_lina_bot"
        assert IntentCategory.STUDY in cap.intents
        assert IntentCategory.RESEARCH in cap.intents
        assert IntentCategory.GENERAL in cap.intents
        assert IntentCategory.COMMAND in cap.intents
        assert IntentCategory.DEV not in cap.intents

    def test_cline_capabilities(self):
        registry = CapabilityRegistry()
        cap = registry.get_capability("cline")
        assert cap is not None
        assert cap.name == "cline"
        assert cap.username == "s_cline_bot"
        assert IntentCategory.DEV in cap.intents
        assert IntentCategory.STUDY in cap.intents
        assert IntentCategory.OPS not in cap.intents

    def test_goose_capabilities(self):
        registry = CapabilityRegistry()
        cap = registry.get_capability("goose")
        assert cap is not None
        assert cap.name == "goose"
        assert cap.username == "s_goose_bot"
        assert IntentCategory.OPS in cap.intents
        assert IntentCategory.DEV in cap.intents

    def test_capability_singleton(self):
        """Dos instancias son la misma."""
        r1 = CapabilityRegistry()
        r2 = CapabilityRegistry()
        assert r1 is r2
        assert id(r1) == id(r2)

    def test_describe_capabilities(self):
        """describe_capabilities incluye todos los bots."""
        registry = CapabilityRegistry()
        desc = registry.describe_capabilities()
        assert "lina" in desc
        assert "cline" in desc
        assert "goose" in desc
        assert "s_lina_bot" in desc
        assert "s_cline_bot" in desc
        assert "s_goose_bot" in desc

    def test_can_handle(self):
        registry = CapabilityRegistry()
        assert registry.can_handle("lina", IntentCategory.STUDY) is True
        assert registry.can_handle("lina", IntentCategory.DEV) is False
        assert registry.can_handle("cline", IntentCategory.DEV) is True
        assert registry.can_handle("goose", IntentCategory.OPS) is True
        assert registry.can_handle("unknown", IntentCategory.GENERAL) is False

    def test_best_bot_for(self):
        registry = CapabilityRegistry()
        best = registry.best_bot_for(IntentCategory.DEV)
        assert best is not None
        assert best.name == "cline"  # cline has fewer intents than goose (more specialized)

        best = registry.best_bot_for(IntentCategory.STUDY)
        assert best is not None
        assert best.name == "lina"  # lina is best for study

    def test_best_bot_for_exclude(self):
        registry = CapabilityRegistry()
        best = registry.best_bot_for(IntentCategory.DEV, exclude="cline")
        assert best is not None
        assert best.name == "goose"  # goose also has DEV

    def test_get_capability_case_insensitive(self):
        registry = CapabilityRegistry()
        assert registry.get_capability("LINA") is not None
        assert registry.get_capability("Cline") is not None

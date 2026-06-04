"""Tests unitarios para lina-knowledge MCP (issue #152).

Estrategia: mockear _execute, _embed y _get_pool para no necesitar DB real ni API key.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set minimal env vars so the module loads without error."""
    monkeypatch.setenv("LINA_DB_URL", "postgresql://mock:mock@localhost:5432/lina")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")


@pytest.fixture()
def mock_execute(monkeypatch):
    """Mock lina_knowledge.server._execute to avoid real PostgreSQL."""
    mock = MagicMock(return_value=None)
    monkeypatch.setattr("lina_knowledge.server._execute", mock)
    return mock


@pytest.fixture()
def mock_embed(monkeypatch):
    """Mock _embed to return a deterministic 1536d vector without API call."""
    fake_vector = [0.042] * 1536
    monkeypatch.setattr("lina_knowledge.server._embed", lambda text: fake_vector)
    return fake_vector


@pytest.fixture(autouse=True)
def mock_pool(monkeypatch):
    """Mock connection pool to avoid real DB connection."""
    monkeypatch.setattr("lina_knowledge.server._get_pool", MagicMock())


# ─── Tests: remember_knowledge ────────────────────────────────────────────────


class TestRememberKnowledge:
    def test_stores_new_knowledge(self, mock_execute, mock_embed):
        from lina_knowledge.server import remember_knowledge

        mock_execute.side_effect = [None, None]  # no existing row → INSERT

        result = remember_knowledge(
            key="test_key_1",
            value="Este es un conocimiento de prueba",
            source="manual",
            tags="test,unit",
            agent="cline",
            ttl_hours=24,
        )

        assert result == "ok"
        # First call: SELECT to check existing
        assert mock_execute.call_count >= 2
        # Verify INSERT was called (second call)
        insert_call = mock_execute.call_args_list[-1]
        assert "INSERT INTO knowledge_store" in str(insert_call)

    def test_updates_existing_knowledge(self, mock_execute, mock_embed):
        from lina_knowledge.server import remember_knowledge

        mock_execute.side_effect = [
            {"id": 1},  # existing row
            None,  # UPDATE
        ]

        result = remember_knowledge(
            key="existing_key",
            value="Valor actualizado",
            source="chat",
            tags="update",
            agent="lina",
        )

        assert result == "updated"
        update_call = mock_execute.call_args_list[-1]
        assert "UPDATE knowledge_store" in str(update_call)

    def test_rejects_empty_key(self):
        from lina_knowledge.server import remember_knowledge

        with pytest.raises(ValueError, match="key no puede estar vacía"):
            remember_knowledge(key="", value="test")

    def test_rejects_empty_value(self):
        from lina_knowledge.server import remember_knowledge

        with pytest.raises(ValueError, match="value no puede estar vacío"):
            remember_knowledge(key="test", value="")

    def test_stores_without_ttl(self, mock_execute, mock_embed):
        from lina_knowledge.server import remember_knowledge

        mock_execute.side_effect = [None, None]

        result = remember_knowledge(
            key="permanent_key",
            value="Esto es permanente",
        )

        assert result == "ok"

    def test_stores_with_tags_empty(self, mock_execute, mock_embed):
        from lina_knowledge.server import remember_knowledge

        mock_execute.side_effect = [None, None]

        result = remember_knowledge(
            key="notags_key",
            value="Sin tags",
            source="web",
            agent="gemma",
        )

        assert result == "ok"


# ─── Tests: search_knowledge ──────────────────────────────────────────────────


class TestSearchKnowledge:
    def test_returns_json_results(self, mock_execute, mock_embed):
        from lina_knowledge.server import search_knowledge

        mock_execute.side_effect = [
            # knowledge_store results
            [
                {
                    "key": "k1",
                    "value": "test value 1",
                    "source": "chat",
                    "agent": "lina",
                    "tags": ["test"],
                    "similarity": 0.95,
                },
                {
                    "key": "k2",
                    "value": "test value 2",
                    "source": "web",
                    "agent": "gemma",
                    "tags": [],
                    "similarity": 0.85,
                },
            ],
            # session_summaries results
            [
                {
                    "session_id": "sess_1",
                    "raw_summary": "Session about testing",
                    "topics": ["testing"],
                    "facts": [],
                    "pending": [],
                    "agent": "lina",
                    "similarity": 0.78,
                },
            ],
            # repo_index results
            [],
        ]

        result = search_knowledge(query="test query", limit=5)
        data = json.loads(result)

        assert "knowledge_store" in data
        assert "session_summaries" in data
        assert "repo_index" in data
        assert len(data["knowledge_store"]) == 2
        assert len(data["session_summaries"]) == 1
        assert len(data["repo_index"]) == 0
        assert data["knowledge_store"][0]["key"] == "k1"

    def test_rejects_empty_query(self):
        from lina_knowledge.server import search_knowledge

        result = json.loads(search_knowledge(query=""))
        assert "error" in result

    def test_filters_by_source(self, mock_execute, mock_embed):
        from lina_knowledge.server import search_knowledge

        mock_execute.side_effect = [[], [], []]

        search_knowledge(query="test", sources="knowledge,repo", limit=3)

        # Only knowledge + repo should be queried (not sessions)
        calls = mock_execute.call_args_list
        call_sqls = [str(c) for c in calls]
        knowledge_calls = [c for c in call_sqls if "knowledge_store" in c]
        repo_calls = [c for c in call_sqls if "repo_index" in c]
        session_calls = [c for c in call_sqls if "session_summaries" in c]

        assert len(knowledge_calls) >= 1
        assert len(repo_calls) >= 1
        assert len(session_calls) == 0  # filtered out


# ─── Tests: recall_knowledge ──────────────────────────────────────────────────


class TestRecallKnowledge:
    def test_returns_results(self, mock_execute, mock_embed):
        from lina_knowledge.server import recall_knowledge

        mock_execute.return_value = [
            {
                "key": "recall_1",
                "value": "Some knowledge about testing",
                "source": "chat",
                "agent": "lina",
                "tags": ["test"],
                "ttl": None,
                "created_at": MagicMock(),
                "similarity": 0.95,
                "text_match": True,
            },
        ]

        result = json.loads(recall_knowledge(query="testing", limit=5))
        assert len(result) == 1
        assert result[0]["key"] == "recall_1"
        assert result[0]["text_match"] is True

        # Verify the SQL contains ILIKE for text matching
        call_args = mock_execute.call_args
        sql = call_args[0][0] if call_args else ""
        assert "ILIKE" in sql or "ilike" in sql.lower()

    def test_filters_by_source_and_agent(self, mock_execute, mock_embed):
        from lina_knowledge.server import recall_knowledge

        mock_execute.return_value = []

        recall_knowledge(query="test", limit=5, source="repo", agent="cline")

        call_args = mock_execute.call_args
        sql = str(call_args)
        assert "source" in sql
        assert "agent" in sql

    def test_rejects_empty_query(self):
        from lina_knowledge.server import recall_knowledge

        result = json.loads(recall_knowledge(query=""))
        assert "error" in result


# ─── Tests: get_knowledge_stats ───────────────────────────────────────────────


class TestGetKnowledgeStats:
    def test_returns_stats(self, mock_execute):
        from lina_knowledge.server import get_knowledge_stats

        mock_execute.side_effect = [
            [{"source": "chat", "cnt": 5}, {"source": "web", "cnt": 3}],
            [{"agent": "lina", "cnt": 4}, {"agent": "gemma", "cnt": 2}],
            {"cnt": 8},
            {"cnt": 1},
            {"cnt": 4},
            {"cnt": 15},
        ]

        result = json.loads(get_knowledge_stats())

        assert result["total_knowledge_entries"] == 8
        assert result["expired_entries"] == 1
        assert result["by_source"]["chat"] == 5
        assert result["by_source"]["web"] == 3
        assert result["by_agent"]["lina"] == 4
        assert result["session_summaries"] == 4
        assert result["repo_index_chunks"] == 15


# ─── Tests: _embed fallback ───────────────────────────────────────────────────


class TestEmbedFallback:
    def test_fallback_zero_vector_without_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        from lina_knowledge.server import _embed

        result = _embed("some text")
        assert len(result) == 1536
        assert all(x == 0.0 for x in result)

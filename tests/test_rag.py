"""RAG 模块测试

覆盖：embedder、vector_store、rag_retrieve_node、rag_answer_node、card builder
"""
import pytest
from unittest.mock import patch, MagicMock, call


class TestEmbedder:
    """Embedding 模型测试"""

    def test_build_article_embed_text_basic(self):
        from app.rag.embedder import build_article_embed_text
        text = build_article_embed_text(
            title="GPT-5 发布",
            vendor="OpenAI",
            summary_points="1. 要点一\n2. 要点二\n3. 要点三",
            channel="Blog",
        )
        assert "OpenAI" in text
        assert "GPT-5 发布" in text
        assert "要点一" in text
        assert "Blog" in text

    def test_build_article_embed_text_no_channel(self):
        from app.rag.embedder import build_article_embed_text
        text = build_article_embed_text(
            title="Test",
            vendor="Anthropic",
            summary_points="",
        )
        assert "Anthropic" in text
        assert "来源:" not in text  # channel 为空时不加

    @patch("app.rag.embedder._get_openai_client")
    def test_get_embedding_success(self, mock_client_fn):
        mock_client = MagicMock()
        mock_embed_response = MagicMock()
        mock_embed_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_embed_response
        mock_client_fn.return_value = mock_client

        from app.rag.embedder import get_embedding
        result = get_embedding("Test text for embedding")
        assert len(result) == 1536
        assert result[0] == 0.1

    @patch("app.rag.embedder._get_openai_client")
    def test_get_embedding_retries_on_failure(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = [
            Exception("API error 1"),
            Exception("API error 2"),
            MagicMock(data=[MagicMock(embedding=[0.2] * 1536)]),
        ]
        mock_client_fn.return_value = mock_client

        from app.rag.embedder import get_embedding
        result = get_embedding("Test")
        assert len(result) == 1536
        assert mock_client.embeddings.create.call_count == 3

    def test_get_embedding_empty_text(self):
        from app.rag.embedder import get_embedding
        result = get_embedding("")
        assert len(result) == 1536
        assert all(v == 0.0 for v in result)


class TestVectorStore:
    """ChromaDB 向量存储测试"""

    def setup_method(self):
        """每个测试前重置 collection"""
        try:
            from app.rag.vector_store import reset_collection
            reset_collection()
        except Exception:
            pass

    def test_get_collection_creates(self):
        from app.rag.vector_store import get_collection, collection_count
        col = get_collection()
        assert col is not None
        assert collection_count() == 0

    def test_add_and_search(self):
        from app.rag.vector_store import add_article, search, collection_count

        # Add two articles with different embeddings
        add_article(
            article_id=1,
            embedding=[0.5] * 1536,
            document="OpenAI GPT-5 发布",
            metadata={"vendor": "OpenAI", "title": "GPT-5 发布", "url": "http://example.com/1", "published_at": "2026-08-01", "channel": "Blog"},
        )
        add_article(
            article_id=2,
            embedding=[0.9] * 1536,
            document="Anthropic Claude 更新",
            metadata={"vendor": "Anthropic", "title": "Claude 更新", "url": "http://example.com/2", "published_at": "2026-08-02", "channel": "Blog"},
        )
        assert collection_count() == 2

        # Search with a query embedding similar to article 1
        results = search([0.5] * 1536, top_k=1)
        assert len(results) == 1
        assert results[0]["article_id"] == 1
        assert results[0]["metadata"]["vendor"] == "OpenAI"

    def test_search_empty_collection(self):
        from app.rag.vector_store import search, collection_count
        assert collection_count() == 0
        results = search([0.5] * 1536)
        assert results == []

    def test_add_article_idempotent(self):
        from app.rag.vector_store import add_article, collection_count

        add_article(
            article_id=1,
            embedding=[0.3] * 1536,
            document="Test",
            metadata={"vendor": "Test", "title": "Test", "url": "http://x.com", "published_at": "", "channel": ""},
        )
        assert collection_count() == 1

        # 重复添加同一 article_id → 幂等（先删后加，count 不变）
        add_article(
            article_id=1,
            embedding=[0.7] * 1536,
            document="Test Updated",
            metadata={"vendor": "Test", "title": "Test", "url": "http://x.com", "published_at": "", "channel": ""},
        )
        assert collection_count() == 1

    def test_add_article_empty_embedding_raises(self):
        from app.rag.vector_store import add_article
        with pytest.raises(ValueError, match="empty or zero vector"):
            add_article(
                article_id=1,
                embedding=[0.0] * 1536,
                document="Test",
                metadata={},
            )


class TestRagRetrieveNode:
    """RAG 检索节点测试"""

    @patch("app.rag.vector_store.collection_count")
    @patch("app.rag.embedder.get_embedding")
    @patch("app.rag.vector_store.search")
    @patch("app.graph.nodes.rag_retrieve._backfill_raw_content")
    @patch("app.graph.nodes.rag_retrieve._emit_retrieve_metric")
    def test_retrieve_with_results(self, mock_metric, mock_backfill, mock_search, mock_embed, mock_count):
        mock_count.return_value = 10
        mock_embed.return_value = [0.1] * 1536
        mock_search.return_value = [
            {"article_id": 1, "document": "Doc 1", "metadata": {"vendor": "OpenAI"}, "distance": 0.1},
        ]
        mock_backfill.return_value = [{
            "article_id": 1, "title": "Test", "vendor": "OpenAI",
            "url": "http://x.com", "published_at": "2026-01-01",
            "channel": "Blog", "summary_points": [], "raw_content": "content",
            "distance": 0.1,
        }]

        from app.graph.nodes.rag_retrieve import rag_retrieve_node
        from app.graph.state import QueryState

        state: QueryState = {
            "user_id": "u1", "chat_id": "c1",
            "user_query": "GPT-5 什么时候发布",
        }
        result = rag_retrieve_node(state)
        assert len(result["rag_context"]) == 1
        assert result["rag_context"][0]["vendor"] == "OpenAI"

    @patch("app.rag.vector_store.collection_count")
    @patch("app.graph.nodes.rag_retrieve._emit_retrieve_metric")
    def test_retrieve_empty_collection(self, mock_metric, mock_count):
        mock_count.return_value = 0

        from app.graph.nodes.rag_retrieve import rag_retrieve_node
        from app.graph.state import QueryState

        state: QueryState = {
            "user_id": "u1", "chat_id": "c1",
            "user_query": "test",
        }
        result = rag_retrieve_node(state)
        assert result["rag_context"] == []

    def test_retrieve_empty_query(self):
        from app.graph.nodes.rag_retrieve import rag_retrieve_node

        state: QueryState = {
            "user_id": "u1", "chat_id": "c1",
            "user_query": "",
        }
        result = rag_retrieve_node(state)
        assert result["rag_context"] == []


class TestRagAnswerNode:
    """RAG 答案生成节点测试"""

    def _make_state(self, context=None, query="test"):
        from app.graph.state import QueryState
        return {"user_id": "u1", "chat_id": "c1", "user_query": query, "rag_context": context or []}

    def test_answer_empty_query(self):
        from app.graph.nodes.rag_answer import rag_answer_node
        state = self._make_state(query="")
        result = rag_answer_node(state)
        assert "你好" in result["rag_answer"]["answer_text"]

    @patch("app.llm.provider.get_llm")
    def test_answer_with_context(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "GPT-5 预计 2026 年底发布 [来源 1]"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        context = [{
            "article_id": 1,
            "title": "GPT-5 Roadmap",
            "vendor": "OpenAI",
            "url": "http://example.com",
            "published_at": "2026-08-01",
            "channel": "Blog",
            "summary_points": ["point 1"],
            "raw_content": "GPT-5 is coming.",
            "distance": 0.1,
        }]

        from app.graph.nodes.rag_answer import rag_answer_node
        state = self._make_state(context=context, query="GPT-5 什么时候发布")
        result = rag_answer_node(state)

        assert "GPT-5" in result["rag_answer"]["answer_text"]
        assert len(result["rag_answer"]["sources"]) == 1
        assert result["rag_answer"]["sources"][0]["vendor"] == "OpenAI"

    def test_answer_no_context(self):
        from app.graph.nodes.rag_answer import rag_answer_node
        state = self._make_state(context=[], query="GPT-5 什么时候发布")
        result = rag_answer_node(state)
        assert "没有找到" in result["rag_answer"]["answer_text"]
        assert result["rag_answer"]["sources"] == []

    def test_build_context_text(self):
        from app.graph.nodes.rag_answer import _build_context_text

        context = [{
            "article_id": 1,
            "title": "Test Article",
            "vendor": "OpenAI",
            "url": "http://x.com",
            "published_at": "2026-01-01",
            "channel": "Blog",
            "summary_points": ["p1", "p2"],
            "raw_content": "Full article content here.",
            "distance": 0.1,
        }]
        text = _build_context_text(context)
        assert "[来源 1]" in text
        assert "Test Article" in text
        assert "OpenAI" in text
        assert "Full article content here." in text

    def test_build_context_text_without_raw_uses_summary(self):
        from app.graph.nodes.rag_answer import _build_context_text

        context = [{
            "article_id": 1,
            "title": "Test",
            "vendor": "OpenAI",
            "url": "http://x.com",
            "published_at": "",
            "channel": "",
            "summary_points": ["point 1", "point 2"],
            "raw_content": "",
            "distance": 0.1,
        }]
        text = _build_context_text(context)
        assert "point 1" in text
        assert "point 2" in text

    def test_extract_sources(self):
        from app.graph.nodes.rag_answer import _extract_sources

        context = [{
            "title": "T1", "vendor": "V1", "url": "http://u1",
            "published_at": "2026-01-01",
        }, {
            "title": "T2", "vendor": "V2", "url": "http://u2",
            "published_at": "",
        }]
        sources = _extract_sources(context)
        assert len(sources) == 2
        assert sources[0]["title"] == "T1"
        assert sources[1]["vendor"] == "V2"


class TestRagAnswerCard:
    """RAG 答案卡片测试"""

    def test_basic_card_structure(self):
        from app.feishu.card_builder import build_rag_answer_card

        card = build_rag_answer_card(
            answer_text="GPT-5 预计 2026 年底发布",
            sources=[
                {"title": "GPT-5 Roadmap", "vendor": "OpenAI", "url": "http://example.com", "published_at": "2026-08-01"},
            ],
            original_query="GPT-5 什么时候发布",
        )
        assert card["header"]["title"]["content"] == "🤖 AI 行业情报"
        assert card["header"]["template"] == "green"
        assert len(card["elements"]) >= 3  # question, answer, sources section

    def test_empty_sources(self):
        from app.feishu.card_builder import build_rag_answer_card

        card = build_rag_answer_card(answer_text="No results")
        assert card["header"]["template"] == "green"
        assert any("参考来源" not in str(e) for e in card["elements"][:2])

    def test_long_answer_truncation(self):
        from app.feishu.card_builder import build_rag_answer_card

        long_answer = "A" * 5000
        card = build_rag_answer_card(answer_text=long_answer)

        # Find the answer element (div with lark_md)
        answer_text = ""
        for elem in card["elements"]:
            if elem.get("tag") == "div" and elem.get("text", {}).get("tag") == "lark_md":
                content = elem["text"]["content"]
                if len(content) > len(answer_text):
                    answer_text = content
        assert len(answer_text) <= 4900  # 4800 chars + truncation suffix

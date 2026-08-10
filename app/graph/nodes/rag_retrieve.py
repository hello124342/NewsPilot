"""RAGRetrieveNode: 语义检索节点

将用户问题 embed 后在 ChromaDB 中检索 top-K 相似文章，
回填 MySQL 中的 raw_content 供 LLM 生成答案。
"""
import logging
import time as _time
from app.graph.state import QueryState

logger = logging.getLogger(__name__)

# 默认检索文章数
_DEFAULT_TOP_K = 5

# 每篇文章 raw_content 截断长度（字符，控制 LLM context token 消耗）
_MAX_CONTENT_CHARS = 2000


def _emit_retrieve_metric(duration: float) -> None:
    """发送检索延迟指标"""
    try:
        from app.core.metrics import rag_retrieve_duration_seconds
        rag_retrieve_duration_seconds.observe(duration)
    except ImportError:
        pass


def rag_retrieve_node(state: QueryState) -> QueryState:
    """语义检索相关文章

    流程：
    1. embed 用户问题
    2. ChromaDB 向量搜索 top-K
    3. MySQL 回填 raw_content
    4. 写入 rag_context
    """
    query = state.get("user_query", "")
    if not query:
        state["rag_context"] = []
        return state

    start = _time.perf_counter()

    try:
        from app.rag.embedder import get_embedding
        from app.rag.vector_store import search, collection_count

        total = collection_count()
        if total == 0:
            logger.info("RAG retrieve: ChromaDB is empty, returning no context")
            state["rag_context"] = []
            return state

        # Step 1: embed 用户问题
        query_embedding = get_embedding(query)

        # Step 2: 语义搜索
        results = search(query_embedding, top_k=_DEFAULT_TOP_K)

        if not results:
            logger.info(f"RAG retrieve: no results for '{query[:50]}'")
            state["rag_context"] = []
            return state

        # Step 3: 回填 raw_content
        context = _backfill_raw_content(results)
        state["rag_context"] = context

        elapsed = _time.perf_counter() - start
        _emit_retrieve_metric(elapsed)

        logger.info(
            f"RAG retrieve: {len(context)} articles in {elapsed:.2f}s "
            f"(query='{query[:50]}', total_docs={total})"
        )

    except Exception as e:
        logger.error(f"RAG retrieve failed: {e}")
        state["rag_context"] = []

    return state


def _backfill_raw_content(results: list[dict]) -> list[dict]:
    """从 MySQL 回填文章的 raw_content

    ChromaDB 只存了摘要 embedding，raw_content 在 MySQL 中。
    检索到相似文章后，回填原文供 LLM 阅读生成答案。
    """
    article_ids = [r["article_id"] for r in results]

    from app.db.database import SessionLocal
    from app.db.models import NewsArticle

    if SessionLocal is None:
        return []

    db = SessionLocal()
    try:
        articles = (
            db.query(NewsArticle)
            .filter(NewsArticle.id.in_(article_ids))
            .all()
        )
        id_to_article = {a.id: a for a in articles}

        context = []
        for r in results:
            aid = r["article_id"]
            article = id_to_article.get(aid)
            raw = article.raw_content if article and article.raw_content else ""
            summary = article.summary_points if article else ""

            context.append({
                "article_id": aid,
                "title": r["metadata"].get("title", ""),
                "vendor": r["metadata"].get("vendor", ""),
                "url": r["metadata"].get("url", ""),
                "published_at": r["metadata"].get("published_at", ""),
                "channel": r["metadata"].get("channel", ""),
                "summary_points": (summary or "").split("\n"),
                "raw_content": raw[:_MAX_CONTENT_CHARS],
                "distance": r.get("distance", 0.0),
            })
        return context
    except Exception as e:
        logger.error(f"_backfill_raw_content failed: {e}")
        return []
    finally:
        db.close()

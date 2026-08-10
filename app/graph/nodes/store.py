"""StoreNode: 数据持久化节点

将已处理的新闻存入 MySQL，并写入 ChromaDB 向量库供 RAG 语义检索。
"""
import hashlib
import logging
from datetime import datetime, timezone
from app.graph.state import PushState
from app.db.models import NewsArticle
from app.core.config import Settings

logger = logging.getLogger(__name__)


def _parse_published_at(date_str: str | None) -> datetime:
    """将 YYYY-MM-DD 字符串转为 datetime，失败时返回当前时间"""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.strptime(date_str.strip()[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def store_node(state: PushState) -> PushState:
    """持久化新闻数据

    将文章元数据和 LLM 摘要存入 MySQL。
    如果 state 中有 published_at，优先使用；否则用当前时间。
    """
    if state.get("status") == "FAILED":
        return state

    settings = Settings()  # type: ignore[call-arg]

    from app.db.database import SessionLocal

    if SessionLocal is None:
        return state

    db = SessionLocal()
    try:
        article = NewsArticle(
            title=state.get("title", "Untitled"),
            url=state["raw_url"],
            url_hash=hashlib.sha256(state["raw_url"].encode()).hexdigest(),
            vendor=state.get("vendor", "Unknown"),
            published_at=_parse_published_at(state.get("published_at")),
            summary_points="\n".join(state.get("summary_points", [])),
            raw_content=state.get("raw_content", ""),
        )
        db.add(article)
        db.commit()
        db.refresh(article)  # 获取自增 id
        state["status"] = "SUCCESS"
        logger.debug(f"Article stored: {state.get('title', '')[:50]} (id={article.id})")

        # --- 写入 ChromaDB 向量库（供 RAG 语义检索） ---
        _embed_to_chromadb(article, state)

    except Exception as e:
        db.rollback()
        # 重复 key（url_hash）说明已存储过，视为成功（跳过 embed）
        if "Duplicate entry" in str(e) or "IntegrityError" in str(e):
            state["status"] = "SUCCESS"
            logger.debug(f"Article already stored (duplicate): {state.get('raw_url', '')[:60]}")
        else:
            logger.error(f"store_node failed for {state.get('raw_url', '')}: {e}")
    finally:
        db.close()

    return state


def _embed_to_chromadb(article: NewsArticle, state: PushState) -> None:
    """将文章写入 ChromaDB 向量库（异步友好，失败不影响主流程）"""
    try:
        from app.rag.embedder import get_embedding, build_article_embed_text
        from app.rag.vector_store import add_article

        embed_text = build_article_embed_text(
            title=article.title,
            vendor=article.vendor,
            summary_points=article.summary_points or "",
            channel=state.get("channel", ""),
        )
        embedding = get_embedding(embed_text)
        add_article(
            article_id=article.id,
            embedding=embedding,
            document=embed_text,
            metadata={
                "vendor": article.vendor,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at.strftime("%Y-%m-%d") if article.published_at else "",
                "channel": state.get("channel", "Blog"),
            },
        )
        logger.debug(f"Article {article.id} embedded to ChromaDB")
    except Exception as e:
        # embedding 失败不阻塞主流程（文章已存入 MySQL，后续可补 embed）
        logger.warning(f"Failed to embed article {article.id} to ChromaDB: {e}")

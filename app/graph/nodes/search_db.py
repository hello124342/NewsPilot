"""SearchDBNode: 数据库检索节点

根据意图解析结果查询 MySQL，获取匹配的新闻文章。
"""
import logging
from datetime import datetime, timedelta, timezone
from app.graph.state import QueryState
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


def search_db_node(state: QueryState) -> QueryState:
    """根据 parsed_intent 查询 MySQL 中的新闻文章

    支持按 vendor 和 days 过滤。
    vendor 为 None 时查询所有厂商，days 默认 3 天。
    """
    intent = state.get("parsed_intent", {})
    vendor = intent.get("vendor")
    days = intent.get("days", 3)

    if SessionLocal is None:
        logger.warning("Database not initialized, returning empty results")
        state["query_results"] = []
        return state

    db = SessionLocal()
    try:
        from app.db.models import NewsArticle

        query = db.query(NewsArticle)
        if vendor:
            query = query.filter(NewsArticle.vendor == vendor)

        # 按时间范围过滤
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(NewsArticle.published_at >= since)

        # 按发布时间倒序
        query = query.order_by(NewsArticle.published_at.desc()).limit(20)

        articles = query.all()
        state["query_results"] = [
            {
                "title": a.title,
                "vendor": a.vendor,
                "published_at": a.published_at.strftime("%Y-%m-%d") if a.published_at else "",
                "url": a.url,
                "summary_points": (a.summary_points or "").split("\n"),
            }
            for a in articles
        ]
        logger.info(
            f"Query results: {len(articles)} articles "
            f"(vendor={vendor}, days={days})"
        )
    except Exception as e:
        logger.error(f"search_db_node failed: {e}")
        state["query_results"] = []
    finally:
        db.close()

    return state

"""SearchDBNode: 数据库检索节点

根据意图解析结果查询 MySQL，获取匹配的新闻文章。
"""
import logging
from datetime import datetime, timedelta, timezone, date
from app.graph.state import QueryState
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


def _calc_since(days: int) -> datetime:
    """计算查询起始时间（日历日边界，00:00:00 UTC）

    days 语义：回溯 N 个日历日（含今天）
    - days=1 → today 00:00 UTC（仅今天）
    - days=3 → (today - 2 days) 00:00 UTC（最近3个日历日）
    - days=7 → (today - 6 days) 00:00 UTC（最近一周）

    这避免了 timedelta(days=N) 从「此刻」精确倒退 N*24h
    导致的「昨天上午的文章被截断」问题。
    """
    # 防御性校验
    if not isinstance(days, int) or days < 1:
        days = 3

    today = date.today()
    since_date = today - timedelta(days=days - 1)
    return datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)


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

        # 按时间范围过滤（日历日边界）
        since = _calc_since(days)
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
            f"(vendor={vendor}, days={days}, since={since.strftime('%Y-%m-%d')})"
        )
    except Exception as e:
        logger.error(f"search_db_node failed: {e}")
        state["query_results"] = []
    finally:
        db.close()

    return state

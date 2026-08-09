"""ExtractNode: 网页正文抓取节点

接收 raw_url，优先使用 RSS feed 自带摘要，无摘要时调用 web_scraper 提取正文。
"""
import logging
from app.graph.state import PushState
from app.fetcher.web_scraper import scrape_article_text

logger = logging.getLogger(__name__)


def extract_node(state: PushState) -> PushState:
    """抓取网页正文并写入 state

    优先使用 RSS 源的 summary（Twitter/Nitter 等源不可直接抓取），
    仅在没有 RSS 摘要时才尝试 Trafilatura 抓取原文。
    """
    url = state["raw_url"]
    rss_summary = state.get("rss_summary", "")

    # 优先使用 RSS feed 自带的内容摘要
    if rss_summary and len(rss_summary) > 20:
        state["raw_content"] = rss_summary
        state["title"] = state.get("title") or url
        logger.debug(f"Using RSS summary for: {url[:60]} ({len(rss_summary)} chars)")
        return state

    # 没有可用摘要 → 尝试抓取原文
    content = scrape_article_text(url)
    if not content:
        state["status"] = "FAILED"
        logger.debug(f"Content extraction failed for: {url[:60]}")
        return state

    state["raw_content"] = content
    state["title"] = state.get("title") or url
    return state

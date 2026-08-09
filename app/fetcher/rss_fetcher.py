"""RSS 订阅源抓取器

解析 RSS/Atom feed，提取文章标题、链接和发布时间。
"""
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
import feedparser

logger = logging.getLogger(__name__)

# RSS URL 到厂商名称的映射
VENDOR_MAP = {
    "openai.com": "OpenAI",
    "anthropic.com": "Anthropic",
    "blog.google": "Google",
    "deepseek.com": "DeepSeek",
    "kimi.com": "Kimi (Moonshot)",
    "meta.com": "Meta",
    "microsoft.com": "Microsoft",
    # Nitter RSS → Twitter 来源
    "nitter.net/OpenAI": "OpenAI",
    "nitter.net/AnthropicAI": "Anthropic",
    "nitter.net/GoogleDeepMind": "Google DeepMind",
    "nitter.net/deepseek_ai": "DeepSeek",
    "nitter.net/MoonshotAI": "Kimi (Moonshot)",
    "nitter.net/zhipuai": "Z.ai / 智谱",
}


def _parse_date_str(date_str: str) -> str:
    """从 RSS 日期字符串中提取 YYYY-MM-DD（email.utils.parsedate 格式）"""
    if not isinstance(date_str, str) or date_str == "Invalid Date":
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    # 最后尝试简单正则匹配
    match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", date_str)
    if match:
        return match.group(1).replace("/", "-")
    return ""


def detect_vendor(rss_url: str) -> str:
    """根据 RSS URL 自动识别厂商名称"""
    for domain, vendor in VENDOR_MAP.items():
        if domain in rss_url:
            return vendor
    return "Unknown"


def fetch_rss_items(rss_url: str) -> list[dict]:
    """拉取并解析 RSS 订阅源

    Args:
        rss_url: RSS 订阅源 URL

    Returns:
        文章列表，每项包含 title, url, published_at
    """
    feed = feedparser.parse(rss_url)
    items = []

    for entry in feed.entries[:20]:  # 一次最多处理 20 篇
        title = entry.get("title", "无标题")

        # RSS 条目中的 link 可能是字符串或列表
        link = entry.get("link", "")
        if isinstance(link, list):
            link = link[0].get("href", "") if link else ""

        # RSS 自带的内容摘要（用于 Twitter/Nitter 等不可抓取的源）
        summary = entry.get("summary", "") or entry.get("description", "")
        if isinstance(summary, str):
            # 清洗 HTML 标签
            import re as _re
            summary = _re.sub(r"<[^>]+>", "", summary).strip()

        # 解析发布时间（feedparser 条目支持 dict 和属性两种访问方式）
        published_at = ""
        pub_parsed = entry.get("published_parsed")
        if pub_parsed:
            try:
                dt = datetime(*pub_parsed[:6])
                published_at = dt.strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass

        # fallback: 从 published 字符串解析（RSSHub 等源 pub_parsed 可能为 None）
        if not published_at:
            pub_str = entry.get("published", "")
            if isinstance(pub_str, str) and pub_str:
                published_at = _parse_date_str(pub_str)

        items.append({
            "title": title,
            "url": link,
            "published_at": published_at,
            "summary": summary,
        })

    logger.debug(f"Fetched {len(items)} items from {rss_url}")
    return items

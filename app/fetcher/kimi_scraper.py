"""Kimi Blog 文章列表爬虫

Kimi (月之暗面) 官方博客没有 RSS feed，通过 HTML 解析获取文章列表。
博客地址: https://www.kimi.com/blog/
"""
import re
import httpx
from datetime import datetime


KIMI_BLOG_URL = "https://www.kimi.com/blog/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _parse_date(text: str) -> str:
    """从文本中提取 YYYY-MM-DD 格式日期"""
    match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", text)
    if match:
        return match.group(1).replace("/", "-")
    return ""


def fetch_kimi_articles() -> list[dict]:
    """抓取 Kimi Blog 文章列表

    Returns:
        文章列表，每项包含 title, url, published_at
    """
    try:
        resp = httpx.get(KIMI_BLOG_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return []

    html = resp.text

    # 从首页提取文章链接
    blog_links = re.findall(r'href="(/blog/[^"]+)"', html)
    # 去重并排除首页自身
    unique_links = list(dict.fromkeys(link for link in blog_links if link != "/blog/"))

    articles = []
    for path in unique_links:
        url = f"https://www.kimi.com{path}"
        title = ""
        published_at = ""

        # 尝试从首页片段提取标题
        link_pattern = re.escape(path)
        context_match = re.search(
            rf'href="{link_pattern}"[^>]*>\s*([^<]+)', html
        )
        if context_match:
            title = context_match.group(1).strip()

        # 获取文章详情页提取日期（只取前 8000 字节，够用）
        try:
            page_resp = httpx.get(url, headers=HEADERS, timeout=10)
            page_resp.raise_for_status()
            page_text = page_resp.text[:20000]

            if not title:
                title_match = re.search(r"<title>([^<]+)</title>", page_text)
                if title_match:
                    title = title_match.group(1).strip()
                    title = re.sub(r"\s*[-|]\s*Kimi.*$", "", title)

            # 优先从 HTML time/meta 标签提取日期
            published_at = ""
            time_match = re.search(r'<time[^>]*datetime="([^"]+)"', page_text)
            if time_match:
                published_at = _parse_date(time_match.group(1))
            if not published_at:
                meta_match = re.search(
                    r'<meta[^>]*property="article:published_time"[^>]*content="([^"]+)"',
                    page_text,
                )
                if meta_match:
                    published_at = _parse_date(meta_match.group(1))
            if not published_at:
                # 全文搜索日期
                published_at = _parse_date(page_resp.text[:50000])
        except Exception:
            pass

        if title and url:
            articles.append({
                "title": title,
                "url": url,
                "published_at": published_at,
            })

    return articles

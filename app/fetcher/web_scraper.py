"""网页正文提取器

基于 Trafilatura 库抓取网页并提取干净正文文本。
支持指数退避重试，超时降级处理。
"""
import time
import trafilatura


def scrape_article_text(url: str, retries: int = 3) -> str:
    """抓取网页并提取正文内容

    采用指数退避重试策略，失败时返回空字符串。

    Args:
        url: 目标网页 URL
        retries: 最大重试次数

    Returns:
        提取的纯文本正文，失败返回空字符串
    """
    for attempt in range(retries):
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded)
                if text:
                    return text.strip()
            return ""

        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 指数退避: 1s, 2s, 4s
            else:
                return ""

    return ""

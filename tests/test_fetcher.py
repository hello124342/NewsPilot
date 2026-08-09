"""数据抓取器测试

测试 RSS 订阅解析和网页正文抓取。
"""
import pytest
from unittest.mock import patch, Mock


class TestRssFetcher:
    """RSS 抓取器测试"""

    @patch("app.fetcher.rss_fetcher.feedparser")
    def test_fetch_rss_items_returns_list(self, mock_feedparser):
        """测试 fetch_rss_items 返回正确格式的列表"""
        mock_feedparser.parse.return_value = _mock_rss_data([
            ("GPT-5 发布", "https://openai.com/gpt-5"),
            ("Claude 4 更新", "https://anthropic.com/claude-4"),
        ])

        from app.fetcher.rss_fetcher import fetch_rss_items

        items = fetch_rss_items("https://example.com/rss.xml")

        assert isinstance(items, list)
        assert len(items) == 2
        assert items[0]["title"] == "GPT-5 发布"
        assert items[0]["url"] == "https://openai.com/gpt-5"
        assert "published_at" in items[0]

    @patch("app.fetcher.rss_fetcher.feedparser")
    def test_fetch_rss_items_empty_feed(self, mock_feedparser):
        """测试空 RSS feed 返回空列表"""
        mock_feedparser.parse.return_value = _mock_rss_data([])

        from app.fetcher.rss_fetcher import fetch_rss_items

        items = fetch_rss_items("https://example.com/empty.xml")
        assert items == []

    @patch("app.fetcher.rss_fetcher.feedparser")
    def test_fetch_rss_items_handles_missing_fields(self, mock_feedparser):
        """测试 RSS 条目缺少 link/title 时优雅处理"""
        from unittest.mock import MagicMock

        feed = MagicMock()
        entry = MagicMock()
        entry.get.side_effect = lambda key, d="": getattr(entry, key) if hasattr(entry, key) else d
        entry.title = "No Link Item"
        entry.link = ""  # 让 hasattr 返回 True，返回空字符串
        entry.published_parsed = None
        feed.entries = [entry]

        mock_feedparser.parse.return_value = feed

        from app.fetcher.rss_fetcher import fetch_rss_items

        items = fetch_rss_items("https://example.com/partial.xml")
        assert len(items) == 1
        assert items[0]["title"] == "No Link Item"
        assert items[0]["url"] == ""  # 无 link 时返回空字符串


class TestWebScraper:
    """网页正文提取测试"""

    @patch("app.fetcher.web_scraper.trafilatura")
    def test_scrape_article_text_success(self, mock_trafilatura):
        """测试成功提取网页正文"""
        mock_trafilatura.fetch_url.return_value = "<html><body><p>Test content</p></body></html>"
        mock_trafilatura.extract.return_value = "提取的正文内容"

        from app.fetcher.web_scraper import scrape_article_text

        result = scrape_article_text("https://example.com/article")
        assert result == "提取的正文内容"

    @patch("app.fetcher.web_scraper.trafilatura")
    def test_scrape_article_text_network_error(self, mock_trafilatura):
        """测试网络异常时返回空字符串"""
        mock_trafilatura.fetch_url.side_effect = Exception("Connection timeout")

        from app.fetcher.web_scraper import scrape_article_text

        result = scrape_article_text("https://broken.example.com")
        assert result == ""

    @patch("app.fetcher.web_scraper.trafilatura")
    def test_scrape_article_text_empty_page(self, mock_trafilatura):
        """测试页面无正文内容时返回空字符串"""
        mock_trafilatura.fetch_url.return_value = "<html><body></body></html>"
        mock_trafilatura.extract.return_value = ""

        from app.fetcher.web_scraper import scrape_article_text

        result = scrape_article_text("https://example.com/empty-page")
        assert result == ""


def _mock_rss_data(entries_spec):
    """构建 mock RSS feedparser 返回数据

    entries_spec: list of (title, link) tuples
    """
    from unittest.mock import MagicMock
    from time import struct_time

    feed = MagicMock()
    entries = []
    for title, link in entries_spec:
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="", _entry=entry: getattr(_entry, key, default)
        entry.title = title
        entry.link = link
        entry.published_parsed = struct_time((2026, 8, 1, 12, 0, 0, 0, 0, 0))
        entries.append(entry)

    feed.entries = entries
    return feed

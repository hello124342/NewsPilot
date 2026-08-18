"""数据库层与 Redis 防重单元测试

测试 SQLAlchemy ORM 模型和 Redis 客户端操作。
使用 SQLite 内存数据库和 mock 验证逻辑正确性。
"""
import hashlib
import pytest
from unittest.mock import Mock, patch, MagicMock

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class TestNewsArticleModel:
    """NewsArticle ORM 模型测试"""

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine

        return create_engine("sqlite:///:memory:", echo=False)

    @pytest.fixture
    def tables(self, engine):
        from app.db.models import Base

        Base.metadata.create_all(engine)
        yield
        Base.metadata.drop_all(engine)

    @pytest.fixture
    def session(self, engine, tables):
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            yield session

    def test_model_table_name(self):
        """测试表名为 news_articles"""
        from app.db.models import NewsArticle

        assert NewsArticle.__tablename__ == "news_articles"

    def test_create_article(self, session):
        """测试创建一条新闻记录"""
        from app.db.models import NewsArticle
        from datetime import datetime

        article = NewsArticle(
            title="GPT-5 发布",
            url="https://openai.com/blog/gpt-5",
            url_hash=_url_hash("https://openai.com/blog/gpt-5"),
            vendor="OpenAI",
            published_at=datetime(2026, 8, 1, 12, 0, 0),
            summary_points="要点一\n要点二\n要点三",
        )
        session.add(article)
        session.commit()

        result = session.query(NewsArticle).filter_by(url="https://openai.com/blog/gpt-5").first()
        assert result is not None
        assert result.title == "GPT-5 发布"
        assert result.vendor == "OpenAI"
        assert result.is_processed is True  # 创建后默认 processed

    def test_url_unique_constraint(self, session):
        """测试 url 字段唯一约束"""
        from app.db.models import NewsArticle
        from sqlalchemy.exc import IntegrityError
        from datetime import datetime

        a1 = NewsArticle(
            title="Article 1",
            url="https://example.com/same-url",
            url_hash=_url_hash("https://example.com/same-url"),
            vendor="OpenAI",
            published_at=datetime(2026, 8, 1),
            summary_points="points",
        )
        a2 = NewsArticle(
            title="Article 2",
            url="https://example.com/same-url",
            url_hash=_url_hash("https://example.com/same-url"),
            vendor="DeepSeek",
            published_at=datetime(2026, 8, 2),
            summary_points="points",
        )
        session.add(a1)
        session.commit()

        session.add(a2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_query_by_vendor(self, session):
        """测试按厂商查询新闻"""
        from app.db.models import NewsArticle
        from datetime import datetime

        session.add_all([
            NewsArticle(title="O1", url="https://o1.com", url_hash=_url_hash("https://o1.com"),
                        vendor="OpenAI",
                        published_at=datetime(2026, 8, 1), summary_points="p"),
            NewsArticle(title="D1", url="https://d1.com", url_hash=_url_hash("https://d1.com"),
                        vendor="DeepSeek",
                        published_at=datetime(2026, 8, 2), summary_points="p"),
            NewsArticle(title="O2", url="https://o2.com", url_hash=_url_hash("https://o2.com"),
                        vendor="OpenAI",
                        published_at=datetime(2026, 8, 3), summary_points="p"),
        ])
        session.commit()

        openai_articles = session.query(NewsArticle).filter_by(vendor="OpenAI").all()
        assert len(openai_articles) == 2

    def test_query_by_date_range(self, session):
        """测试按日期范围查询新闻"""
        from app.db.models import NewsArticle
        from datetime import datetime

        session.add_all([
            NewsArticle(title="Old", url="https://old.com", url_hash=_url_hash("https://old.com"),
                        vendor="OpenAI",
                        published_at=datetime(2026, 7, 20), summary_points="p"),
            NewsArticle(title="New", url="https://new.com", url_hash=_url_hash("https://new.com"),
                        vendor="OpenAI",
                        published_at=datetime(2026, 8, 5), summary_points="p"),
        ])
        session.commit()

        from datetime import date

        recent = (
            session.query(NewsArticle)
            .filter(NewsArticle.published_at >= date(2026, 8, 1))
            .all()
        )
        assert len(recent) == 1
        assert recent[0].title == "New"


class TestRedisClient:
    """Redis 客户端单元测试（使用 mock）"""

    @pytest.fixture
    def mock_redis(self):
        with patch("app.db.redis.redis.Redis") as mock:
            yield mock

    @pytest.fixture
    def client(self, mock_redis):
        from app.db.redis import RedisClient

        return RedisClient(host="127.0.0.1", port=6379)

    def test_is_url_processed_true(self, client, mock_redis):
        """测试 URL 已处理时返回 True"""
        mock_redis.return_value.sismember.return_value = True

        result = client.is_url_processed("https://example.com/article")
        assert result is True
        mock_redis.return_value.sismember.assert_called_with(
            "feishu_bot:processed_urls", "https://example.com/article"
        )

    def test_is_url_processed_false(self, client, mock_redis):
        """测试 URL 未处理时返回 False"""
        mock_redis.return_value.sismember.return_value = False

        result = client.is_url_processed("https://example.com/new-article")
        assert result is False

    def test_mark_url_processed(self, client, mock_redis):
        """测试标记 URL 为已处理"""
        client.mark_url_processed("https://example.com/article")

        mock_redis.return_value.sadd.assert_called_with(
            "feishu_bot:processed_urls", "https://example.com/article"
        )

    def test_cache_token(self, client, mock_redis):
        """测试缓存飞书 token"""
        client.cache_token("test-token-xxx", ttl=7200)

        mock_redis.return_value.setex.assert_called_with(
            "feishu_bot:tenant_access_token", 7200, "test-token-xxx"
        )

    def test_get_cached_token_hit(self, client, mock_redis):
        """测试获取已缓存的 token"""
        mock_redis.return_value.get.return_value = "cached-token-xxx"

        result = client.get_cached_token()
        assert result == "cached-token-xxx"

    def test_get_cached_token_miss(self, client, mock_redis):
        """测试缓存未命中时返回 None"""
        mock_redis.return_value.get.return_value = None

        result = client.get_cached_token()
        assert result is None

    def test_redis_url_connection_string(self, mock_redis):
        """测试自定义 host/port 能正确连接"""
        from app.db.redis import RedisClient

        client = RedisClient(host="redis.local", port=6380)
        # 触发连接
        _ = client.is_url_processed("test")
        mock_redis.assert_called_with(host="redis.local", port=6380, decode_responses=True)


class TestIndexes:
    """索引测试

    (platform, conversation_id) 是多平台改造后的主查询键，此前完全没有索引；
    news_articles 按 published_at 过滤+倒序，同样缺索引 —— 数据量上来后都是全表扫描。
    """

    @pytest.fixture
    def inspector(self):
        from sqlalchemy import create_engine, inspect
        from app.db.models import Base

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)
        return inspect(engine)

    def _index_columns(self, inspector, table: str) -> list[list[str]]:
        return [idx["column_names"] for idx in inspector.get_indexes(table)]

    @pytest.mark.parametrize("table", ["subscriptions", "chat_preferences", "chat_registry"])
    def test_platform_conversation_composite_index(self, inspector, table):
        """三张多平台表都有 (platform, conversation_id) 复合索引"""
        assert ["platform", "conversation_id"] in self._index_columns(inspector, table)

    def test_news_articles_published_at_indexed(self, inspector):
        """deliver_job / search_db 按 published_at 过滤排序"""
        cols = self._index_columns(inspector, "news_articles")
        assert ["published_at"] in cols
        assert ["vendor", "published_at"] in cols

    def test_chat_registry_active_scan_index(self, inspector):
        """推送时按 (platform, is_active) 扫描全部活跃会话"""
        assert ["platform", "is_active"] in self._index_columns(inspector, "chat_registry")

    def test_index_migrations_cover_all_model_indexes(self):
        """存量库靠 _INDEX_MIGRATIONS 补齐，必须与模型定义的索引一一对应"""
        from app.db.database import _INDEX_MIGRATIONS
        from app.db.models import Base

        model_indexes = {
            idx.name
            for table in Base.metadata.tables.values()
            for idx in table.indexes
            if idx.name.endswith(("_platform_conv", "_platform_active", "_published_at", "_vendor_published"))
        }
        migration_indexes = {m["index"] for m in _INDEX_MIGRATIONS}
        assert model_indexes == migration_indexes

    def test_index_migrations_are_idempotent(self):
        """索引已存在时跳过，不重复执行 CREATE INDEX"""
        from sqlalchemy import create_engine, inspect
        from app.db.models import Base
        import app.db.database as database

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)  # 建表时索引已随之创建

        with patch.object(database, "engine", engine):
            # 不抛异常即为通过；所有索引都应被识别为已存在
            database._run_index_migrations(inspect(engine))

        # 索引数量未因重复执行而变化
        after = {idx["name"] for idx in inspect(engine).get_indexes("chat_registry")}
        assert "ix_chat_registry_platform_conv" in after

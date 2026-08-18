"""SQLAlchemy 数据库会话配置

引擎和会话工厂的创建与工具函数。
"""
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import Settings

logger = logging.getLogger(__name__)

# 轻量迁移：需要检测的 MySQL 列（增量式，幂等安全）
_MIGRATIONS: list[dict] = [
    {
        "table": "news_articles",
        "column": "raw_content",
        "sql": "ALTER TABLE news_articles ADD COLUMN raw_content TEXT COMMENT '文章原始全文（供 RAG 检索和引用）'",
    },
    # ========== 多平台支持迁移 ==========
    {
        "table": "subscriptions",
        "column": "platform",
        "sql": "ALTER TABLE subscriptions ADD COLUMN platform VARCHAR(32) NOT NULL DEFAULT 'feishu' COMMENT '平台标识：feishu / telegram'",
    },
    {
        "table": "subscriptions",
        "column": "conversation_id",
        "sql": "ALTER TABLE subscriptions ADD COLUMN conversation_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '平台原生的会话ID'",
    },
    {
        "table": "chat_preferences",
        "column": "platform",
        "sql": "ALTER TABLE chat_preferences ADD COLUMN platform VARCHAR(32) NOT NULL DEFAULT 'feishu' COMMENT '平台标识：feishu / telegram'",
    },
    {
        "table": "chat_preferences",
        "column": "conversation_id",
        "sql": "ALTER TABLE chat_preferences ADD COLUMN conversation_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '平台原生的会话ID'",
    },
    {
        "table": "chat_registry",
        "column": "platform",
        "sql": "ALTER TABLE chat_registry ADD COLUMN platform VARCHAR(32) NOT NULL DEFAULT 'feishu' COMMENT '平台标识：feishu / telegram'",
    },
    {
        "table": "chat_registry",
        "column": "conversation_id",
        "sql": "ALTER TABLE chat_registry ADD COLUMN conversation_id VARCHAR(128) NOT NULL DEFAULT '' COMMENT '平台原生的会话ID'",
    },
    # 数据回填：将现有 chat_id 复制到 conversation_id（仅空行）
    {
        "table": "subscriptions",
        "column": "conversation_id_backfill",
        "sql": "UPDATE subscriptions SET conversation_id = chat_id WHERE conversation_id = '' AND chat_id IS NOT NULL AND chat_id != ''",
    },
    {
        "table": "chat_preferences",
        "column": "conversation_id_backfill",
        "sql": "UPDATE chat_preferences SET conversation_id = chat_id WHERE conversation_id = '' AND chat_id IS NOT NULL AND chat_id != ''",
    },
    {
        "table": "chat_registry",
        "column": "conversation_id_backfill",
        "sql": "UPDATE chat_registry SET conversation_id = chat_id WHERE conversation_id = '' AND chat_id IS NOT NULL AND chat_id != ''",
    },
]

# 索引迁移：与列迁移分开，因为检测方式不同（get_indexes 而非 get_columns）
# 说明：(platform, conversation_id) 是多平台改造后的主查询键，此前完全没有索引；
# news_articles 按 published_at 过滤+倒序，同样缺索引，数据量上来后是全表扫描。
_INDEX_MIGRATIONS: list[dict] = [
    {
        "table": "news_articles",
        "index": "ix_news_articles_published_at",
        "sql": "CREATE INDEX ix_news_articles_published_at ON news_articles (published_at)",
    },
    {
        "table": "news_articles",
        "index": "ix_news_articles_vendor_published",
        "sql": "CREATE INDEX ix_news_articles_vendor_published ON news_articles (vendor, published_at)",
    },
    {
        "table": "subscriptions",
        "index": "ix_subscriptions_platform_conv",
        "sql": "CREATE INDEX ix_subscriptions_platform_conv ON subscriptions (platform, conversation_id)",
    },
    {
        "table": "chat_preferences",
        "index": "ix_chat_preferences_platform_conv",
        "sql": "CREATE INDEX ix_chat_preferences_platform_conv ON chat_preferences (platform, conversation_id)",
    },
    {
        "table": "chat_registry",
        "index": "ix_chat_registry_platform_conv",
        "sql": "CREATE INDEX ix_chat_registry_platform_conv ON chat_registry (platform, conversation_id)",
    },
    {
        "table": "chat_registry",
        "index": "ix_chat_registry_platform_active",
        "sql": "CREATE INDEX ix_chat_registry_platform_active ON chat_registry (platform, is_active)",
    },
]


def _run_migrations():
    """检测并补全缺失的数据库列与索引（不更改已有对象，安全幂等）"""
    if engine is None or SessionLocal is None:
        logger.warning("Database not initialized, skipping migrations")
        return

    inspector = inspect(engine)
    for migration in _MIGRATIONS:
        table = migration["table"]
        column = migration["column"]
        try:
            cols = [c["name"] for c in inspector.get_columns(table)]
            if column not in cols:
                logger.info(f"Running migration: {migration['sql']}")
                with engine.connect() as conn:
                    conn.execute(text(migration["sql"]))
                    conn.commit()
                logger.info(f"Migration complete: {table}.{column} added")
            else:
                logger.debug(f"Migration skipped: {table}.{column} already exists")
        except Exception as e:
            logger.error(f"Migration check failed for {table}.{column}: {e}")

    _run_index_migrations(inspector)


def _run_index_migrations(inspector):
    """补全缺失的索引（幂等：已存在则跳过）

    新建库由 Base.metadata.create_all() 直接带上索引，这里只处理存量库。
    """
    for migration in _INDEX_MIGRATIONS:
        table = migration["table"]
        index = migration["index"]
        try:
            existing = {idx["name"] for idx in inspector.get_indexes(table)}
            if index in existing:
                logger.debug(f"Index migration skipped: {index} already exists")
                continue
            logger.info(f"Running index migration: {migration['sql']}")
            with engine.connect() as conn:
                conn.execute(text(migration["sql"]))
                conn.commit()
            logger.info(f"Index migration complete: {index} created on {table}")
        except Exception as e:
            logger.error(f"Index migration failed for {table}.{index}: {e}")


def create_engine_from_settings(settings: Settings):
    """根据配置创建 SQLAlchemy 引擎"""
    return create_engine(
        settings.mysql_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


def create_session_factory(engine):
    """创建线程安全的 Session 工厂"""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# 全局引擎和工厂（应用启动时通过 settings 初始化）
engine = None
SessionLocal: sessionmaker | None = None


def init_db(settings: Settings):
    """初始化数据库引擎和会话工厂（应用启动时调用）"""
    global engine, SessionLocal
    engine = create_engine_from_settings(settings)
    SessionLocal = create_session_factory(engine)


def get_db_session() -> Session:
    """获取一个新的数据库会话上下文管理器"""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

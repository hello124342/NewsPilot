"""SQLAlchemy 数据库会话配置

引擎和会话工厂的创建与工具函数。
"""
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import Settings

logger = logging.getLogger(__name__)

# 轻量迁移：需要检测的 MySQL 列
_MIGRATIONS: list[dict] = [
    {
        "table": "news_articles",
        "column": "raw_content",
        "sql": "ALTER TABLE news_articles ADD COLUMN raw_content TEXT COMMENT '文章原始全文（供 RAG 检索和引用）'",
    },
]


def _run_migrations():
    """检测并补全缺失的数据库列（不更改已有列，安全幂等）"""
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

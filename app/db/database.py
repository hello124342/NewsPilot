"""SQLAlchemy 数据库会话配置

引擎和会话工厂的创建与工具函数。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import Settings


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

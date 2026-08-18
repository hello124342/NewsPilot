"""SQLAlchemy Repository 实现

将 subscription/handler.py 和 chat/lifecycle.py 中的 CRUD 逻辑
提取到 Repository 接口的具体实现中。

保持与原代码一致的行为：
- 相同的事务管理（可选 db 参数支持 session 复用）
- 相同的 TTL 缓存集成
- 相同的错误处理和日志
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import Subscription, ChatPreference, ChatRegistry
from app.db.repositories import SubscriptionRepository, ChatRegistryRepository
from app.core.cache import chat_meta_cache, chat_pref_cache

logger = logging.getLogger(__name__)


# ========== SQL Subscription Repository ==========

class SqlSubscriptionRepository(SubscriptionRepository):
    """订阅管理的 SQLAlchemy 实现"""

    # 厂商列表和别名（与 handler.py 保持一致）
    ALL_VENDORS = [
        "OpenAI", "Anthropic", "Google DeepMind",
        "DeepSeek", "Kimi (Moonshot)", "Z.ai / 智谱",
    ]

    VENDOR_ALIASES: dict[str, str] = {
        "openai": "OpenAI", "anthropic": "Anthropic", "claude": "Anthropic",
        "google": "Google DeepMind", "deepmind": "Google DeepMind",
        "google deepmind": "Google DeepMind", "deepseek": "DeepSeek",
        "deep seek": "DeepSeek", "kimi": "Kimi (Moonshot)",
        "moonshot": "Kimi (Moonshot)", "kimi (moonshot)": "Kimi (Moonshot)",
        "z.ai": "Z.ai / 智谱", "智谱": "Z.ai / 智谱",
        "z.ai / 智谱": "Z.ai / 智谱", "智谱ai": "Z.ai / 智谱",
    }

    PUSH_TIMES = {"09:00": "早上 9:00", "12:00": "中午 12:00", "18:00": "下午 6:00"}
    FREQUENCIES = {"daily": "每天", "weekdays": "仅工作日", "weekly_monday": "每周一汇总"}

    TIME_ALIASES: dict[str, str] = {
        "早上9点": "09:00", "早上9:00": "09:00", "9点": "09:00", "9:00": "09:00",
        "上午9点": "09:00", "上午9:00": "09:00",
        "中午12点": "12:00", "中午12:00": "12:00", "12点": "12:00", "12:00": "12:00",
        "下午6点": "18:00", "下午6:00": "18:00", "晚上6点": "18:00", "6点": "18:00",
        "18点": "18:00", "18:00": "18:00", "傍晚6点": "18:00",
    }

    FREQ_ALIASES: dict[str, str] = {
        "每天": "daily", "每日": "daily", "天天": "daily",
        "工作日": "weekdays", "仅工作日": "weekdays", "上班日": "weekdays",
        "每周": "weekly_monday", "每周一": "weekly_monday",
        "每周一汇总": "weekly_monday", "周一": "weekly_monday",
    }

    # ---- 厂商别名解析 ----

    def _resolve_vendor(self, text: str) -> str | None:
        text = text.strip().lower()
        if not text:
            return None
        if text in self.VENDOR_ALIASES:
            return self.VENDOR_ALIASES[text]
        for std_name in self.ALL_VENDORS:
            if text in std_name.lower():
                return std_name
        return None

    # ---- 订阅 CRUD ----

    def subscribe(self, chat_id: str, vendor: str, db: Session | None = None,
                  platform: str = "feishu") -> str:
        if SessionLocal is None:
            return "❌ 系统未初始化，请稍后再试"

        def _do(session: Session):
            existing = session.query(Subscription).filter_by(
                chat_id=chat_id, vendor=vendor, platform=platform
            ).first()
            if existing:
                if existing.is_active:
                    return f"✅ 你已经订阅了 **{vendor}**，无需重复订阅"
                existing.is_active = True
                session.commit()
                return f"✅ 已重新订阅 **{vendor}**"

            sub = Subscription(
                platform=platform, conversation_id=chat_id,
                chat_id=chat_id, vendor=vendor, is_active=True,
            )
            session.add(sub)
            session.commit()
            logger.info(f"Subscribed: platform={platform}, chat_id={chat_id}, vendor={vendor}")
            return f"✅ 已订阅 **{vendor}**，你将在每日推送中收到相关新闻"

        if db is not None:
            try:
                return _do(db)
            except Exception as e:
                db.rollback()
                logger.error(f"subscribe failed: {e}")
                return "❌ 订阅失败，请稍后再试"

        session = SessionLocal()
        try:
            return _do(session)
        except Exception as e:
            session.rollback()
            logger.error(f"subscribe failed: {e}")
            return "❌ 订阅失败，请稍后再试"
        finally:
            session.close()

    def unsubscribe(self, chat_id: str, vendor: str, db: Session | None = None,
                    platform: str = "feishu") -> str:
        if SessionLocal is None:
            return "❌ 系统未初始化，请稍后再试"

        def _do(session: Session):
            existing = session.query(Subscription).filter_by(
                chat_id=chat_id, vendor=vendor, is_active=True, platform=platform
            ).first()
            if not existing:
                return f"⚠️ 你当前未订阅 **{vendor}**"
            existing.is_active = False
            session.commit()
            logger.info(f"Unsubscribed: platform={platform}, chat_id={chat_id}, vendor={vendor}")
            return f"🔕 已退订 **{vendor}**，你不再收到相关新闻"

        if db is not None:
            try:
                return _do(db)
            except Exception as e:
                db.rollback()
                logger.error(f"unsubscribe failed: {e}")
                return "❌ 退订失败，请稍后再试"

        session = SessionLocal()
        try:
            return _do(session)
        except Exception as e:
            session.rollback()
            logger.error(f"unsubscribe failed: {e}")
            return "❌ 退订失败，请稍后再试"
        finally:
            session.close()

    def list_active(self, chat_id: str, db: Session | None = None,
                    platform: str = "feishu") -> list[str]:
        if SessionLocal is None:
            return []

        def _query(session: Session):
            subs = session.query(Subscription).filter_by(
                chat_id=chat_id, is_active=True, platform=platform
            ).all()
            return [s.vendor for s in subs]

        if db is not None:
            return _query(db)

        session = SessionLocal()
        try:
            return _query(session)
        except Exception as e:
            logger.error(f"list_active failed: {e}")
            return []
        finally:
            session.close()

    def get_subscribers(self, vendor: str, platform: str = "feishu") -> list[str]:
        if SessionLocal is None:
            return []

        db = SessionLocal()
        try:
            subs = db.query(Subscription).filter_by(
                vendor=vendor, is_active=True, platform=platform
            ).all()
            return [s.conversation_id or s.chat_id for s in subs]
        except Exception as e:
            logger.error(f"get_subscribers failed: {e}")
            return []
        finally:
            db.close()

    def has_any(self, chat_id: str, platform: str = "feishu") -> bool:
        if SessionLocal is None:
            return False

        db = SessionLocal()
        try:
            count = db.query(Subscription).filter_by(
                chat_id=chat_id, platform=platform
            ).count()
            return count > 0
        except Exception:
            return False
        finally:
            db.close()

    # ---- 推送偏好 ----

    def get_preference(self, chat_id: str, db: Session | None = None,
                       platform: str = "feishu") -> dict:
        cache_key = f"{platform}:{chat_id}:pref"
        cached = chat_pref_cache.get(cache_key)
        if cached is not None:
            return cached

        if SessionLocal is None:
            return {"push_time": "09:00", "frequency": "daily"}

        def _query(session: Session):
            pref = session.query(ChatPreference).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            result = (
                {"push_time": pref.push_time, "frequency": pref.frequency}
                if pref
                else {"push_time": "09:00", "frequency": "daily"}
            )
            chat_pref_cache.set(cache_key, result)
            return result

        if db is not None:
            return _query(db)

        session = SessionLocal()
        try:
            return _query(session)
        except Exception as e:
            logger.error(f"get_preference failed: {e}")
            return {"push_time": "09:00", "frequency": "daily"}
        finally:
            session.close()

    def set_push_time(self, chat_id: str, push_time: str, db: Session | None = None,
                      platform: str = "feishu") -> dict:
        if SessionLocal is None:
            return {"push_time": push_time, "frequency": "daily"}

        def _do(session: Session):
            pref = session.query(ChatPreference).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            if pref:
                pref.push_time = push_time
            else:
                pref = ChatPreference(
                    platform=platform, conversation_id=chat_id,
                    chat_id=chat_id, push_time=push_time,
                )
                session.add(pref)
            session.commit()
            result = {"push_time": pref.push_time, "frequency": pref.frequency}
            chat_pref_cache.set(f"{platform}:{chat_id}:pref", result)
            logger.info(f"Push time set: platform={platform}, chat_id={chat_id}, time={push_time}")
            return result

        if db is not None:
            try:
                return _do(db)
            except Exception as e:
                db.rollback()
                logger.error(f"set_push_time failed: {e}")
                return {"push_time": push_time, "frequency": "daily"}

        session = SessionLocal()
        try:
            return _do(session)
        except Exception as e:
            session.rollback()
            logger.error(f"set_push_time failed: {e}")
            return {"push_time": push_time, "frequency": "daily"}
        finally:
            session.close()

    def set_frequency(self, chat_id: str, frequency: str, db: Session | None = None,
                      platform: str = "feishu") -> dict:
        if SessionLocal is None:
            return {"push_time": "09:00", "frequency": frequency}

        def _do(session: Session):
            pref = session.query(ChatPreference).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            if pref:
                pref.frequency = frequency
            else:
                pref = ChatPreference(
                    platform=platform, conversation_id=chat_id,
                    chat_id=chat_id, frequency=frequency,
                )
                session.add(pref)
            session.commit()
            result = {"push_time": pref.push_time, "frequency": pref.frequency}
            chat_pref_cache.set(f"{platform}:{chat_id}:pref", result)
            logger.info(f"Frequency set: platform={platform}, chat_id={chat_id}, freq={frequency}")
            return result

        if db is not None:
            try:
                return _do(db)
            except Exception as e:
                db.rollback()
                logger.error(f"set_frequency failed: {e}")
                return {"push_time": "09:00", "frequency": frequency}

        session = SessionLocal()
        try:
            return _do(session)
        except Exception as e:
            session.rollback()
            logger.error(f"set_frequency failed: {e}")
            return {"push_time": "09:00", "frequency": frequency}
        finally:
            session.close()

    # ---- 命令检测（纯逻辑，无 DB 依赖）----

    def get_all_vendors(self) -> list[str]:
        return list(self.ALL_VENDORS)

    def detect_command(self, text: str):
        """检测用户消息是否为订阅命令（与 handler.py detect_command 逻辑一致）"""
        import re
        text = text.strip()

        if text in ("订阅所有", "訂閱所有"):
            return ("subscribe", "__ALL__")
        if text in ("取消订阅所有", "取消訂閱所有", "退订所有", "退訂所有"):
            return ("unsubscribe", "__ALL__")

        # 列表命令（必须在「订阅 xxx」之前检查）
        if re.match(r"^(我的订阅|我的訂閱|订阅列表|訂閱列表|订阅状态|訂閱狀態)$", text):
            return ("list", None)

        # 订阅 xxx
        m = re.match(r"^(订阅|訂閱)\s*(.+)$", text)
        if m:
            vendor = self._resolve_vendor(m.group(2))
            if vendor:
                return ("subscribe", vendor)
            return None

        # 退订 xxx
        m = re.match(r"^(取消订阅|取消訂閱|退订|退訂)\s*(.+)$", text)
        if m:
            vendor = self._resolve_vendor(m.group(2))
            if vendor:
                return ("unsubscribe", vendor)
            return None

        # 设置命令
        result = self._detect_settings(text)
        if result:
            return result

        return None

    def _detect_settings(self, text: str):
        import re
        text = text.strip()

        if re.match(r"^(设置|推送设置|偏好设置)$", text):
            return ("settings", None)

        m = re.match(r"^(设置推送时间|推送时间|设置时间)\s*(.+)$", text)
        if m:
            alias = m.group(2).strip().lower()
            time_val = self.TIME_ALIASES.get(alias)
            if time_val:
                return ("set_time", time_val)
            return None

        m = re.match(r"^(设置推送频率|推送频率|设置频率)\s*(.+)$", text)
        if m:
            alias = m.group(2).strip().lower()
            freq_val = self.FREQ_ALIASES.get(alias)
            if freq_val:
                return ("set_freq", freq_val)
            return None

        return None

    def is_today_in_frequency(self, frequency: str) -> bool:
        from datetime import date
        today = date.today()
        weekday = today.weekday()
        if frequency == "daily":
            return True
        if frequency == "weekdays":
            return weekday < 5
        if frequency == "weekly_monday":
            return weekday == 0
        return True


# ========== SQL Chat Registry Repository ==========

class SqlChatRegistryRepository(ChatRegistryRepository):
    """Chat 注册表管理的 SQLAlchemy 实现"""

    def register(self, chat_id: str, chat_type: Literal["group", "user"] = "group",
                 platform: str = "feishu") -> bool:
        if SessionLocal is None:
            return False

        db = SessionLocal()
        try:
            existing = db.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            if existing:
                existing.is_active = True
                existing.last_active_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Chat re-activated: {platform}/{chat_id} ({chat_type})")
                return False

            entry = ChatRegistry(
                platform=platform, conversation_id=chat_id,
                chat_id=chat_id, chat_type=chat_type, is_active=True,
            )
            db.add(entry)
            db.commit()
            chat_meta_cache.set(f"{platform}:{chat_id}:type", chat_type)
            logger.info(f"Chat registered: {platform}/{chat_id} ({chat_type})")
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"register failed: {e}")
            return False
        finally:
            db.close()

    def deactivate(self, chat_id: str, platform: str = "feishu") -> None:
        if SessionLocal is None:
            return

        db = SessionLocal()
        try:
            entry = db.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            if entry:
                entry.is_active = False
                db.commit()
                chat_meta_cache.delete(f"{platform}:{chat_id}:type")
                chat_meta_cache.delete(f"{platform}:{chat_id}:owner")
                logger.info(f"Chat deactivated: {platform}/{chat_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"deactivate failed: {e}")
        finally:
            db.close()

    def is_new(self, chat_id: str, platform: str = "feishu") -> bool:
        if SessionLocal is None:
            return True

        db = SessionLocal()
        try:
            exists = db.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            return exists is None
        except Exception:
            return True
        finally:
            db.close()

    def get_active_chats(self, platform: str | None = "feishu") -> list[dict]:
        """获取活跃 chat 列表

        Args:
            platform: 限定平台；None 表示不限（跨平台调用方需自行按 platform 分流）

        必须按平台过滤：会话 ID 只在本平台内有意义，把 Discord 频道 ID
        交给 FeishuClient 发送只会得到一串无效目标。
        """
        if SessionLocal is None:
            return []

        db = SessionLocal()
        try:
            query = db.query(ChatRegistry).filter(ChatRegistry.is_active == True)
            if platform is not None:
                query = query.filter(ChatRegistry.platform == platform)
            return [
                {
                    "chat_id": e.conversation_id or e.chat_id,
                    "chat_type": e.chat_type,
                    "platform": e.platform or "feishu",
                }
                for e in query.all()
            ]
        except Exception as e:
            logger.error(f"get_active_chats failed: {e}")
            return []
        finally:
            db.close()

    def get_active_chat_ids(self, platform: str | None = "feishu") -> list[str]:
        return [c["chat_id"] for c in self.get_active_chats(platform=platform)]

    def get_type(self, chat_id: str, db: Session | None = None,
                 platform: str = "feishu") -> str | None:
        cache_key = f"{platform}:{chat_id}:type"
        cached = chat_meta_cache.get(cache_key)
        if cached is not None:
            return cached

        if SessionLocal is None:
            return None

        def _query(session: Session):
            entry = session.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            result = entry.chat_type if entry else None
            if result:
                chat_meta_cache.set(cache_key, result)
            return result

        if db is not None:
            return _query(db)

        session = SessionLocal()
        try:
            return _query(session)
        except Exception:
            return None
        finally:
            session.close()

    def get_owner_id(self, chat_id: str, db: Session | None = None,
                     platform: str = "feishu") -> str | None:
        cache_key = f"{platform}:{chat_id}:owner"
        cached = chat_meta_cache.get(cache_key)
        if cached is not None:
            return cached

        if SessionLocal is None:
            return None

        def _query(session: Session):
            entry = session.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            result = entry.owner_id if entry else None
            if result:
                chat_meta_cache.set(cache_key, result)
            return result

        if db is not None:
            return _query(db)

        session = SessionLocal()
        try:
            return _query(session)
        except Exception:
            return None
        finally:
            session.close()

    def set_owner_id(self, chat_id: str, owner_id: str,
                     platform: str = "feishu") -> None:
        if SessionLocal is None:
            return

        db = SessionLocal()
        try:
            entry = db.query(ChatRegistry).filter_by(
                chat_id=chat_id, platform=platform
            ).first()
            if entry:
                entry.owner_id = owner_id
                db.commit()
                chat_meta_cache.set(f"{platform}:{chat_id}:owner", owner_id)
        except Exception as e:
            db.rollback()
            logger.error(f"set_owner_id failed: {e}")
        finally:
            db.close()

    def can_manage_subscription(
        self, chat_id: str, sender_id: str,
        db: Session | None = None,
        feishu_client=None,
        platform: str = "feishu",
        platform_adapter=None,
    ) -> bool:
        """检查 sender 是否有权限管理此 chat 的订阅

        权限模型：
          - 私聊：总是有权限
          - 群聊：仅群主/管理员有权限
          - 未知类型：fail-open（有权限）

        平台适配：
          - 飞书：通过 feishu_client.get_chat_info() 查询群主 open_id
          - Telegram：通过 platform_adapter.is_admin() 检查管理员身份
          - feishu_client 参数保留向后兼容（过渡期）
        """
        chat_type = self.get_type(chat_id, db=db, platform=platform)

        if chat_type == "user":
            return True
        if chat_type is None:
            return True

        # Telegram / Discord: 使用 platform_adapter.is_admin()
        if platform in ("telegram", "discord") and platform_adapter:
            try:
                return platform_adapter.is_admin(chat_id, sender_id)
            except Exception:
                return True  # fail open

        # 飞书: 缓存 owner_id 对比
        owner_id = self.get_owner_id(chat_id, db=db, platform=platform)
        if not owner_id and feishu_client:
            try:
                info = feishu_client.get_chat_info(chat_id)
                if info and info.get("owner_id"):
                    self.set_owner_id(chat_id, info["owner_id"], platform=platform)
                    return sender_id == info["owner_id"]
            except Exception:
                pass
            return True  # fail open

        return sender_id == owner_id if owner_id else True


# ========== 模块级默认实例（兼容现有代码）==========

# 这些实例由 handler.py / lifecycle.py 的 facade 函数使用
# 测试时可以通过 replace_repos() 替换为 mock 实例

_sub_repo: SubscriptionRepository | None = None
_chat_repo: ChatRegistryRepository | None = None


def get_subscription_repo() -> SubscriptionRepository:
    """获取 SubscriptionRepository 的当前实现"""
    global _sub_repo
    if _sub_repo is None:
        _sub_repo = SqlSubscriptionRepository()
    return _sub_repo


def get_chat_repo() -> ChatRegistryRepository:
    """获取 ChatRegistryRepository 的当前实现"""
    global _chat_repo
    if _chat_repo is None:
        _chat_repo = SqlChatRegistryRepository()
    return _chat_repo


def replace_repos(
    sub_repo: SubscriptionRepository | None = None,
    chat_repo: ChatRegistryRepository | None = None,
) -> None:
    """替换全局 repository 实例（供测试使用）

    Args:
        sub_repo: 新的订阅 repository（None 表示不替换）
        chat_repo: 新的 chat repository（None 表示不替换）
    """
    global _sub_repo, _chat_repo
    if sub_repo is not None:
        _sub_repo = sub_repo
    if chat_repo is not None:
        _chat_repo = chat_repo

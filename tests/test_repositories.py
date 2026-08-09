"""Repository 模式测试

测试 Repository ABC 接口 + SQL 实现 + Facade 委托。
"""
import pytest
from unittest.mock import MagicMock, patch

from app.db.repositories import SubscriptionRepository, ChatRegistryRepository
from app.db.sql_repositories import (
    SqlSubscriptionRepository,
    SqlChatRegistryRepository,
    get_subscription_repo,
    get_chat_repo,
    replace_repos,
)


class TestSubscriptionRepositoryABC:
    """订阅 Repository 接口测试"""

    def test_abc_cannot_instantiate(self):
        """ABC 不能直接实例化"""
        with pytest.raises(TypeError):
            SubscriptionRepository()  # type: ignore[abstract]

    def test_sql_repo_implements_interface(self):
        """SQL 实现满足接口"""
        repo = SqlSubscriptionRepository()
        assert isinstance(repo, SubscriptionRepository)

    def test_all_methods_implemented(self):
        """所有抽象方法都有实现"""
        repo = SqlSubscriptionRepository()
        # 验证关键方法存在
        assert callable(repo.subscribe)
        assert callable(repo.unsubscribe)
        assert callable(repo.list_active)
        assert callable(repo.get_subscribers)
        assert callable(repo.has_any)
        assert callable(repo.get_preference)
        assert callable(repo.set_push_time)
        assert callable(repo.set_frequency)
        assert callable(repo.detect_command)
        assert callable(repo.is_today_in_frequency)

    def test_mock_repository(self):
        """Mock Repository 可用于测试业务逻辑（依赖倒置验证）"""
        mock_repo = MagicMock(spec=SubscriptionRepository)
        mock_repo.list_active.return_value = ["OpenAI", "DeepSeek"]
        mock_repo.has_any.return_value = True

        # 业务逻辑使用 mock（不依赖 SQLAlchemy）
        subs = mock_repo.list_active("chat_001")
        assert subs == ["OpenAI", "DeepSeek"]
        assert mock_repo.has_any("chat_001") is True


class TestChatRegistryRepositoryABC:
    """Chat Repository 接口测试"""

    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            ChatRegistryRepository()  # type: ignore[abstract]

    def test_sql_repo_implements_interface(self):
        repo = SqlChatRegistryRepository()
        assert isinstance(repo, ChatRegistryRepository)

    def test_all_methods_implemented(self):
        repo = SqlChatRegistryRepository()
        assert callable(repo.register)
        assert callable(repo.deactivate)
        assert callable(repo.is_new)
        assert callable(repo.get_active_chats)
        assert callable(repo.get_type)
        assert callable(repo.get_owner_id)
        assert callable(repo.set_owner_id)
        assert callable(repo.can_manage_subscription)


class TestRepositoryReplace:
    """测试 Repository 替换机制（供测试使用）"""

    def test_replace_and_restore(self):
        """替换 repository 并恢复"""
        original_sub = get_subscription_repo()
        original_chat = get_chat_repo()

        mock_sub = MagicMock(spec=SubscriptionRepository)
        mock_chat = MagicMock(spec=ChatRegistryRepository)

        replace_repos(sub_repo=mock_sub, chat_repo=mock_chat)
        assert get_subscription_repo() is mock_sub
        assert get_chat_repo() is mock_chat

        # 恢复
        replace_repos(sub_repo=original_sub, chat_repo=original_chat)
        assert get_subscription_repo() is original_sub


class TestFacadeDelegation:
    """Facade 函数委托测试 — handler.py / lifecycle.py 函数委托到 Repository"""

    @patch("app.db.sql_repositories.SessionLocal")
    def test_handler_subscribe_delegates_to_repo(self, mock_session):
        """handler.subscribe() 委托到 Repository"""
        from app.subscription.handler import subscribe

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db

        result = subscribe("chat_test", "OpenAI")
        assert "已订阅" in result

    @patch("app.db.sql_repositories.SessionLocal")
    def test_lifecycle_register_delegates_to_repo(self, mock_session):
        """lifecycle.register_chat() 委托到 Repository"""
        from app.chat.lifecycle import register_chat

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db

        is_new = register_chat("chat_new", "group")
        assert is_new is True

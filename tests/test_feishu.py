"""飞书 SDK 与卡片构造器测试

测试飞书 API 客户端（基于 lark-oapi SDK）和卡片 JSON 生成。
"""
import json
import pytest
from unittest.mock import patch, Mock, MagicMock


class TestFeishuClient:
    """飞书 Open API 客户端测试（基于 lark-oapi SDK）"""

    @pytest.fixture
    def settings(self):
        from app.core.config import Settings
        return Settings(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="test_secret",
            FEISHU_CHAT_IDS="oc_aaa,oc_bbb",
        )

    @pytest.fixture
    def client(self, settings):
        """创建 FeishuClient 并注入 mock SDK client"""
        with patch("app.feishu.client.lark.Client") as mock_lark_client:
            from app.feishu.client import FeishuClient
            feishu = FeishuClient(settings)
            # 替换内部 SDK client 为 mock
            feishu._client = mock_lark_client.builder.return_value.app_id.return_value.app_secret.return_value.log_level.return_value.build.return_value
            yield feishu

    def test_get_tenant_access_token_is_noop(self, client):
        """测试 get_tenant_access_token 返回空字符串（SDK 自动管理 token）"""
        token = client.get_tenant_access_token()
        assert token == ""

    def test_send_card(self, client):
        """测试使用 SDK 发送交互式卡片"""
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.code = 0
        mock_response.msg = "success"
        client._client.im.v1.message.create.return_value = mock_response

        card = {"header": {"title": "test"}}
        result = client.send_card("oc_xxx", card)
        assert result["code"] == 0

        # 验证 SDK 被调用了一次
        assert client._client.im.v1.message.create.called

    def test_send_card_failure_raises(self, client):
        """测试 SDK 返回错误时抛出 RuntimeError"""
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad request"
        client._client.im.v1.message.create.return_value = mock_response

        with pytest.raises(RuntimeError, match="send_card failed"):
            client.send_card("oc_xxx", {"header": {}})

    def test_send_card_to_all(self, client):
        """测试批量发送卡片到所有配置的 chat_id"""
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.code = 0
        mock_response.msg = "success"
        client._client.im.v1.message.create.return_value = mock_response

        card = {"header": {"title": "test"}}
        results = client.send_card_to_all(card)

        assert len(results) == 2  # FEISHU_CHAT_IDS="oc_aaa,oc_bbb"
        assert all(r["code"] == 0 for r in results)

    def test_send_card_to_all_empty_chat_ids(self, client):
        """测试无配置 chat_id 时返回空列表"""
        client.settings.FEISHU_CHAT_IDS = ""
        results = client.send_card_to_all({})
        assert results == []

    def test_get_chat_info_success(self, client):
        """测试成功获取群信息"""
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data.chat_id = "oc_test"
        mock_response.data.owner_id = "ou_owner"
        mock_response.data.name = "Test Group"
        client._client.im.v1.chat.get.return_value = mock_response

        info = client.get_chat_info("oc_test")
        assert info is not None
        assert info["chat_id"] == "oc_test"
        assert info["owner_id"] == "ou_owner"
        assert info["name"] == "Test Group"

    def test_get_chat_info_failure(self, client):
        """测试获取群信息失败返回 None"""
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 404
        client._client.im.v1.chat.get.return_value = mock_response

        info = client.get_chat_info("oc_nonexistent")
        assert info is None

    def test_get_chat_info_exception(self, client):
        """测试获取群信息异常返回 None"""
        client._client.im.v1.chat.get.side_effect = Exception("Network error")

        info = client.get_chat_info("oc_error")
        assert info is None


class TestCardBuilder:
    """飞书卡片 JSON 构建器测试"""

    def test_build_news_card_basic(self):
        """测试基本卡片 JSON 结构"""
        from app.feishu.card_builder import build_news_card

        card = build_news_card(
            title="GPT-5 发布",
            vendor="OpenAI",
            summary_points=["突破性推理能力", "多模态支持", "成本降低50%"],
            raw_url="https://openai.com/blog/gpt-5",
            published_at="2026-08-01",
        )

        assert "header" in card
        assert card["header"]["title"]["tag"] == "plain_text"
        # Plan A: header 仅显示厂商名，标题在 body 中
        assert card["header"]["title"]["content"] == "OpenAI"

        # 验证标题在 elements 中
        elements_text = " ".join(
            e.get("text", {}).get("content", "")
            for e in card["elements"]
        )
        assert "GPT-5 发布" in elements_text

        assert "elements" in card

        # 验证原文链接在 action 中
        actions = card["elements"][-1]
        assert actions["tag"] == "action"
        button = actions["actions"][0]
        assert button["tag"] == "button"
        assert button["url"] == "https://openai.com/blog/gpt-5"

    def test_build_news_card_summary_content(self):
        """测试卡片正文包含三个要点"""
        from app.feishu.card_builder import build_news_card

        points = ["要点A：内容摘要", "要点B：技术细节", "要点C：影响分析"]
        card = build_news_card(
            title="Test",
            vendor="DeepSeek",
            summary_points=points,
            raw_url="https://example.com",
            published_at="2026-08-01",
        )

        # 递归提取卡片中所有文本内容
        def extract_text(obj):
            if isinstance(obj, str):
                return obj
            if isinstance(obj, dict):
                return " ".join(extract_text(v) for v in obj.values())
            if isinstance(obj, list):
                return " ".join(extract_text(i) for i in obj)
            return ""
        all_text = extract_text(card)

        for p in points:
            assert p in all_text

    def test_build_news_card_has_read_more_button(self):
        """测试卡片包含「阅读原文」按钮"""
        from app.feishu.card_builder import build_news_card

        card = build_news_card(
            title="Test",
            vendor="Anthropic",
            summary_points=["要点"],
            raw_url="https://example.com/article",
            published_at="2026-08-01",
        )

        actions = card["elements"][-1]
        assert actions["tag"] == "action"
        btn = actions["actions"][0]
        assert "阅读" in btn["text"]["content"] or "原文" in btn["text"]["content"]
        assert btn["url"] == "https://example.com/article"

    def test_build_news_card_includes_published_date(self):
        """测试卡片包含发布时间信息"""
        from app.feishu.card_builder import build_news_card

        card = build_news_card(
            title="Test",
            vendor="OpenAI",
            summary_points=["要点"],
            raw_url="https://example.com",
            published_at="2026-08-01",
        )

        def extract_text(obj):
            if isinstance(obj, str):
                return obj
            if isinstance(obj, dict):
                return " ".join(extract_text(v) for v in obj.values())
            if isinstance(obj, list):
                return " ".join(extract_text(i) for i in obj)
            return ""

        all_text = extract_text(card)
        assert "2026-08-01" in all_text

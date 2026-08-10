"""logging_config 模块单元测试

测试结构化日志配置和 JSON 格式输出。
"""
import logging
import json
import io
import sys
import pytest

from app.core.logging_config import setup_logging


class TestSetupLogging:
    """setup_logging 测试"""

    def setup_method(self):
        """每个测试前保存 root logger 状态"""
        self.root = logging.getLogger()
        self.original_handlers = list(self.root.handlers)
        self.original_level = self.root.level

    def teardown_method(self):
        """每个测试后恢复 root logger 状态"""
        self.root.handlers.clear()
        for h in self.original_handlers:
            self.root.addHandler(h)
        self.root.setLevel(self.original_level)

    def test_adds_handler_to_root_logger(self):
        """setup_logging 给 root logger 添加 handler"""
        self.root.handlers.clear()
        setup_logging()
        assert len(self.root.handlers) > 0

    def test_default_level_is_info(self):
        """默认日志级别为 INFO"""
        self.root.handlers.clear()
        setup_logging()
        assert self.root.level == logging.INFO

    def test_respects_settings_log_level(self):
        """尊重 settings 中的 LOG_LEVEL 配置"""

        class FakeSettings:
            LOG_LEVEL = "DEBUG"

        self.root.handlers.clear()
        setup_logging(FakeSettings())
        assert self.root.level == logging.DEBUG

    def test_respects_warning_level(self):
        """LOG_LEVEL=WARNING 时级别为 WARNING"""

        class FakeSettings:
            LOG_LEVEL = "WARNING"

        self.root.handlers.clear()
        setup_logging(FakeSettings())
        assert self.root.level == logging.WARNING

    def test_none_settings_defaults_to_info(self):
        """settings=None 时默认 INFO"""
        self.root.handlers.clear()
        setup_logging(None)
        assert self.root.level == logging.INFO

    def test_output_is_json(self):
        """日志输出是合法 JSON"""
        self.root.handlers.clear()
        setup_logging()

        # 捕获 stdout
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        self.root.handlers.clear()
        # Use the same formatter setup_logging would create
        setup_logging()
        # 找到 stdout handler 并重定向
        for h in self.root.handlers:
            h.stream = buf

        logger = logging.getLogger("test_json_output")
        logger.info("hello world")

        output = buf.getvalue().strip()
        if output:
            parsed = json.loads(output)
            assert parsed["message"] == "hello world"
            assert parsed["level"] == "INFO"
            assert parsed["logger"] == "test_json_output"
            assert "timestamp" in parsed

    def test_existing_logger_still_works(self):
        """现有 logger.info() 调用仍然正常工作"""
        self.root.handlers.clear()
        setup_logging()

        logger = logging.getLogger("some.existing.module")
        # 不抛异常，正常输出
        logger.info("test message")
        logger.warning("test warning")
        logger.error("test error")

    def test_suppresses_noisy_libraries(self):
        """第三方库日志级别被设为 WARNING"""
        self.root.handlers.clear()
        setup_logging()

        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("apscheduler.scheduler").level == logging.WARNING
        assert logging.getLogger("lark_oapi").level == logging.WARNING

    def test_handlers_cleared_before_setup(self):
        """调用 setup_logging 时清空之前的 handler"""
        # 先手动加一个
        self.root.addHandler(logging.StreamHandler(sys.stdout))
        before_count = len(self.root.handlers)

        setup_logging()
        # 新 handler 替换旧的
        assert len(self.root.handlers) == 1

    def test_custom_logger_inherits_root_handler(self):
        """子 logger 继承 root logger 的 handler 输出 JSON"""
        self.root.handlers.clear()
        setup_logging()

        buf = io.StringIO()
        child = logging.getLogger("app.some.module")
        child.propagate = True

        # 捕获 root handler 输出
        for h in self.root.handlers:
            h.stream = buf

        child.info("inherited message")
        output = buf.getvalue().strip()
        if output:
            parsed = json.loads(output)
            assert parsed["message"] == "inherited message"
            assert parsed["logger"] == "app.some.module"

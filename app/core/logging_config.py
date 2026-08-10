"""结构化日志配置模块

替换 main.py 中的 logging.basicConfig，提供 JSON 格式的结构化日志。
支持通过 LOG_LEVEL 环境变量动态调整日志级别。

用法（在 main.py 最早位置调用一次）:
    from app.core.logging_config import setup_logging
    setup_logging(settings)
"""
import logging
import sys
import json
from datetime import datetime, timezone

from pythonjsonlogger import json as jsonlogger


class _StructuredFormatter(jsonlogger.JsonFormatter):
    """自定义 JSON 格式化器"""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        # 统一时间字段名为 timestamp，ISO8601 格式
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        # 确保 message 字段存在
        if "message" not in log_record or not log_record["message"]:
            log_record["message"] = record.getMessage()


def setup_logging(settings=None) -> None:
    """初始化结构化日志

    必须在任何其他模块 logging.getLogger() 之前调用。
    所有现有 logger.info(msg) 调用保持兼容——消息会自动提取为 JSON 的 message 字段。

    Args:
        settings: 应用配置。为 None 时不读取 LOG_LEVEL，使用默认 INFO。
    """
    log_level_name = "INFO"
    if settings is not None:
        log_level_name = getattr(settings, "LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    # 清空 root logger 的所有 handler（包括 basicConfig 设的）
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    formatter = _StructuredFormatter(
        "%(timestamp)s %(level)s %(logger)s %(message)s",
        json_encoder=json.JSONEncoder,
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # 抑制第三方库的噪音日志
    for noisy_lib in [
        "urllib3",
        "urllib3.connectionpool",
        "httpx",
        "httpcore",
        "apscheduler.scheduler",
        "apscheduler.executors.default",
        "lark_oapi",
        "lark_oapi.ws",
    ]:
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

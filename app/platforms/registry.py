"""平台适配器注册与发现

参考 app/llm/provider.py 的 Factory 模式：
  - 通过平台名获取对应的适配器实例
  - 支持延迟加载，避免循环导入
  - 未配置的平台自动静默跳过

使用方式：
  adapter = get_platform_adapter("feishu", settings)
  adapter.send_message(chat_id, rich_message)
"""

import logging
from typing import Type

from app.core.config import Settings
from app.platforms.adapter import PlatformAdapter

logger = logging.getLogger(__name__)

# 平台名 → 适配器类的注册表（延迟导入，避免循环依赖）
_PLATFORM_ADAPTER_REGISTRY: dict[str, str] = {
    "feishu": "app.platforms.feishu.adapter:FeishuAdapter",
    "telegram": "app.platforms.telegram.adapter:TelegramAdapter",
}

# 平台名 → 是否需要配置才能启用
_PLATFORM_REQUIRES_CONFIG: dict[str, list[str]] = {
    "feishu": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
    "telegram": ["TELEGRAM_BOT_TOKEN"],
}


def _import_adapter_class(platform: str) -> Type[PlatformAdapter] | None:
    """从注册表动态导入适配器类"""
    import importlib

    spec = _PLATFORM_ADAPTER_REGISTRY.get(platform)
    if not spec:
        logger.warning(f"Unknown platform: {platform}")
        return None

    module_path, class_name = spec.split(":")
    try:
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        logger.error(f"Failed to import adapter for '{platform}': {e}")
        return None


def is_platform_configured(platform: str, settings: Settings | None = None) -> bool:
    """检查指定平台的凭证是否已配置

    Args:
        platform: 平台标识符
        settings: 应用配置，为 None 时自动创建

    Returns:
        True 表示平台已配置可用
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    required_keys = _PLATFORM_REQUIRES_CONFIG.get(platform, [])
    if not required_keys:
        return True

    for key in required_keys:
        value = getattr(settings, key, "")
        if not value or not value.strip():
            return False
    return True


def get_platform_adapter(
    platform: str,
    settings: Settings | None = None,
) -> PlatformAdapter | None:
    """获取平台适配器实例

    如果平台未配置（凭证缺失），返回 None 并记录 info 日志。

    Args:
        platform: 平台标识符 "feishu" / "telegram"
        settings: 应用配置

    Returns:
        PlatformAdapter 实例，或 None（平台未配置）

    Raises:
        ValueError: 平台标识符未在注册表中
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    if platform not in _PLATFORM_ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown platform '{platform}'. "
            f"Supported: {list(_PLATFORM_ADAPTER_REGISTRY.keys())}"
        )

    if not is_platform_configured(platform, settings):
        logger.info(
            f"Platform '{platform}' is not configured, skipping. "
            f"Required: {_PLATFORM_REQUIRES_CONFIG.get(platform, [])}"
        )
        return None

    adapter_class = _import_adapter_class(platform)
    if adapter_class is None:
        return None

    return adapter_class(settings)


def list_available_platforms(settings: Settings | None = None) -> list[str]:
    """返回当前已配置的所有平台

    Args:
        settings: 应用配置，为 None 时自动创建

    Returns:
        已配置的平台标识符列表
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    available = []
    for platform in _PLATFORM_ADAPTER_REGISTRY:
        if is_platform_configured(platform, settings):
            available.append(platform)
    return available


def list_all_platforms() -> list[str]:
    """返回注册表中所有支持的平台名（无论是否配置）"""
    return list(_PLATFORM_ADAPTER_REGISTRY.keys())

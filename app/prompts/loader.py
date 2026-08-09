"""Prompt 模板加载器

从 YAML 文件加载 Prompt 模板，支持版本管理和热加载。
"""
import logging
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

# Prompt 文件目录
PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """加载指定名称的 Prompt 模板

    Args:
        name: Prompt 文件名（不含 .yaml 扩展名），如 "summarize"、"intent"

    Returns:
        模板字符串（含 {variable} 占位符）

    Raises:
        FileNotFoundError: Prompt 文件不存在
        KeyError: YAML 中缺少 template 字段
    """
    prompt_file = PROMPTS_DIR / f"{name}.yaml"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    with open(prompt_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "template" not in data:
        raise KeyError(f"Prompt '{name}' missing 'template' field")

    logger.debug(f"Loaded prompt: {name} (v{data.get('version', 'unknown')})")
    return data["template"]

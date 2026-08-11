"""平台适配层 (Platform Adapter Layer)

提供统一的跨平台消息接口，使核心业务逻辑（RSS抓取 → LLM摘要 → 推送）与具体
IM 平台（飞书、Telegram、Slack 等）解耦。

架构：
  adapter.py      — PlatformAdapter ABC（核心接口定义）
  message_model.py — RichMessage, ActionButton, CallbackData, ConversationInfo
  registry.py     — 平台注册与发现（参考 app/llm/provider.py 的 Factory 模式）
  feishu/         — 飞书适配器实现
  telegram/       — Telegram 适配器实现
"""

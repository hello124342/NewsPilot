"""LangGraph State 状态定义

包含 NewsPushGraph 和 BotQueryGraph 两个工作流的状态类型。
"""
from typing import TypedDict


class PushState(TypedDict, total=False):
    """新闻推送工作流状态

    从抓取到推送的完整链路数据载体。
    """
    raw_url: str          # 原始新闻 URL
    raw_content: str      # 抓取的网页正文
    rss_summary: str      # RSS feed 自带摘要（优先使用，无需抓取原文）
    vendor: str           # 识别出的 AI 厂商
    title: str            # 新闻标题
    published_at: str     # 发布时间字符串（YYYY-MM-DD）
    channel: str          # 渠道类型（Blog | Twitter）
    summary_points: list[str]  # LLM 生成的核心要点（3 条）
    card_json: dict       # 飞书卡片 JSON
    status: str           # 执行状态：PENDING / SUCCESS / FAILED


class QueryState(TypedDict, total=False):
    """飞书交互查询工作流状态

    @Bot 消息触发的查询链路数据载体。
    """
    user_id: str          # 触发用户 ID
    chat_id: str          # 群聊 / 单聊 ID
    user_query: str       # 用户输入的原始文本
    parsed_intent: dict   # LLM 解析出的查询条件 {"vendor": str, "days": int}
    query_results: list[dict]  # 数据库查询结果
    reply_card_json: dict     # 回复卡片 JSON

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
    platform: str          # 平台标识: "feishu" | "telegram"
    user_id: str           # 触发用户 ID
    chat_id: str           # 群聊 / 单聊 ID
    user_query: str        # 用户输入的原始文本
    query_type: str        # 意图分类: "list" | "qa"
    parsed_intent: dict    # LLM 解析出的查询条件 {"vendor": str, "days": int}
    query_results: list[dict]  # 数据库查询结果（list 路径使用）
    rag_context: list[dict]    # RAG 检索到的文章上下文（qa 路径使用）
    rag_answer: dict           # RAG 答案 {"answer_text": str, "sources": list[dict]}
    reply_card_json: dict      # [过渡期] 回复卡片 JSON（飞书特定格式，逐步迁移到 rich_message）
    rich_message: dict         # 平台无关的 RichMessage（序列化为 dict）

"""飞书 Interactive Card 富文本卡片构造器

方案 A 风格：
  Header: 厂商名称
  Body:   渠道图标 + 日期 → 文章标题 → 核心要点
  Footer: 阅读原文按钮
"""

CHANNEL_ICON = {
    "Blog": "📰",
    "Twitter": "🐦",
}


def build_news_card(
    title: str = "",
    vendor: str = "",
    summary_points: list[str] | None = None,
    raw_url: str = "",
    published_at: str = "",
    channel: str = "Blog",
) -> dict:
    """构建新闻推送的飞书 Interactive Card JSON（方案 A 风格）

    Args:
        title: 文章/推文标题
        vendor: 来源厂商名称
        summary_points: LLM 总结的要点列表（3 条）
        raw_url: 原文链接
        published_at: 发布时间字符串（YYYY-MM-DD）
        channel: 渠道类型（Blog | Twitter）

    Returns:
        飞书 Interactive Card JSON (dict)
    """
    if summary_points is None:
        summary_points = []

    icon = CHANNEL_ICON.get(channel, "📰")
    # 渠道 + 日期行
    if published_at:
        meta_line = f"{icon} **{channel}** · {published_at}"
    else:
        meta_line = f"{icon} **{channel}**"

    # 核心要点
    points_md = "\n".join(
        f"  {i}. {point}" for i, point in enumerate(summary_points, 1)
    ) if summary_points else "暂无摘要"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": vendor,
            },
        },
        "elements": [
            # 渠道 + 日期
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": meta_line},
            },
            {"tag": "hr"},
            # 文章标题
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{title}**"},
            },
            {"tag": "hr"},
            # 要点标题
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "💡 **核心要点总结**"},
            },
            # 三条要点
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": points_md},
            },
            {"tag": "hr"},
            # 阅读原文按钮
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📖 阅读原文"},
                        "type": "primary",
                        "url": raw_url,
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"🔕 退订 {vendor}"},
                        "type": "default",
                        "value": {"action": "unsubscribe", "vendor": vendor},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 设置"},
                        "type": "default",
                        "value": {"action": "settings"},
                    },
                ],
            },
        ],
    }

    return card


# ========== 订阅相关卡片 ==========


def build_subscription_reply(action: str, vendor: str) -> dict:
    """订阅/退订操作的确认卡片

    Args:
        action: "subscribe" 或 "unsubscribe"
        vendor: 厂商名称

    Returns:
        飞书卡片 JSON
    """
    is_subscribe = action == "subscribe"
    icon = "✅" if is_subscribe else "🔕"
    verb = "订阅" if is_subscribe else "退订"

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{icon} 已{verb} **{vendor}**",
            },
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"你将在每日推送中收到 **{vendor}** 的相关新闻。"
                    if is_subscribe
                    else f"你不再收到 **{vendor}** 的相关新闻。"
                ),
            },
        },
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 发送「我的订阅」查看当前订阅列表",
                }
            ],
        },
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if is_subscribe else "blue",
            "title": {"tag": "plain_text", "content": f"{verb}确认"},
        },
        "elements": elements,
    }


def build_subscription_list_card(subscribed: list[str]) -> dict:
    """当前订阅列表卡片

    Args:
        subscribed: 已订阅的厂商名列表

    Returns:
        飞书卡片 JSON
    """
    if subscribed:
        vendor_lines = "\n".join(f"  • {v}" for v in subscribed)
        body = f"**你当前订阅了以下 {len(subscribed)} 个厂商：**\n\n{vendor_lines}"
    else:
        body = "⚠️ **你当前没有订阅任何厂商**\n\n发送「订阅 OpenAI」开始订阅"

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": body},
        },
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 发送「订阅 <厂商名>」或「退订 <厂商名>」管理订阅",
                }
            ],
        },
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "📋 我的订阅"},
        },
        "elements": elements,
    }


def build_welcome_card() -> dict:
    """首次使用引导卡片（私聊）

    Returns:
        飞书卡片 JSON
    """
    vendor_lines = "\n".join(
        f"  • {v}" for v in ["OpenAI", "Anthropic", "Google DeepMind", "DeepSeek", "Kimi (Moonshot)", "Z.ai / 智谱"]
    )
    body = (
        "👋 **欢迎使用 AI 新闻 Bot！**\n\n"
        "我每天 **9:00** 推送 AI 厂商的最新动态。\n\n"
        "**可订阅的厂商：**\n"
        f"{vendor_lines}\n\n"
        "**快速上手：**\n"
        "  • 发送「**订阅 OpenAI**」订阅\n"
        "  • 发送「**订阅所有**」订阅全部\n"
        "  • 发送「**订阅列表**」查看状态\n"
        "  • 发送「**OpenAI 最近有什么新闻**」查询\n\n"
        "⏰ 可在下方设置面板中修改推送时间和频率"
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "🤖 AI 新闻 Bot"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body},
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 推送设置"},
                        "type": "primary",
                        "value": {"action": "settings"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📋 管理订阅"},
                        "type": "default",
                        "value": {"action": "subs:manage"},
                    },
                ],
            },
        ],
    }


def build_settings_card(
    subscribed: list[str],
    push_time: str = "09:00",
    frequency: str = "daily",
) -> dict:
    """推送设置控制面板卡片

    Args:
        subscribed: 已订阅的厂商名列表
        push_time: 当前推送时间 "09:00" / "12:00" / "18:00"
        frequency: 当前频率 "daily" / "weekdays" / "weekly_monday"

    Returns:
        飞书 Interactive Card JSON
    """
    from app.subscription.handler import PUSH_TIMES, FREQUENCIES

    time_label = PUSH_TIMES.get(push_time, push_time)
    freq_label = FREQUENCIES.get(frequency, frequency)

    # 时间按钮（当前选中的高亮）
    time_buttons = []
    for t_val, t_label in PUSH_TIMES.items():
        prefix = "✅ " if t_val == push_time else ""
        time_buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"{prefix}{t_label}"},
            "type": "primary" if t_val == push_time else "default",
            "value": {"action": "set_time", "time": t_val},
        })

    # 频率按钮
    freq_buttons = []
    for f_val, f_label in FREQUENCIES.items():
        prefix = "✅ " if f_val == frequency else ""
        freq_buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"{prefix}{f_label}"},
            "type": "primary" if f_val == frequency else "default",
            "value": {"action": "set_freq", "freq": f_val},
        })

    # 订阅列表
    if subscribed:
        sub_lines = "、".join(subscribed)
        sub_text = f"📌 **已订阅：** {sub_lines}"
    else:
        sub_text = "⚠️ 尚未订阅任何厂商"

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"⏰ **推送时间**\n当前：{time_label}"},
        },
        {"tag": "action", "actions": time_buttons},
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"📅 **推送频率**\n当前：{freq_label}"},
        },
        {"tag": "action", "actions": freq_buttons},
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": sub_text},
        },
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 也可以发送「设置推送时间 9点」快速设置",
                }
            ],
        },
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "⚙️ 推送设置"},
        },
        "elements": elements,
    }


def build_group_welcome_card() -> dict:
    """群聊入驻欢迎卡片（Bot 被拉入群时发送）

    默认已订阅全部厂商，告知群成员如何退订和修改设置。
    """
    vendors_str = "、".join([
        "OpenAI", "Anthropic", "Google DeepMind", "DeepSeek", "Kimi (Moonshot)", "Z.ai / 智谱"
    ])

    body = (
        "👋 **我开始追踪 6 家 AI 厂商动态！**\n\n"
        "每天早上 **9:00** 自动推送最新新闻摘要。\n\n"
        f"📌 **已默认订阅：** {vendors_str}\n\n"
        "**群管理：**\n"
        "  • 💬 @Bot **订阅 OpenAI** — 只看某个厂商\n"
        "  • 💬 @Bot **退订 DeepSeek** — 取消某个厂商\n"
        "  • 💬 @Bot **订阅列表** — 查看当前订阅\n"
        "  • ⏰ 修改推送时间或频率\n\n"
        "**所有成员：**\n"
        "  • 💬 @Bot **OpenAI 最近有什么新闻** — 查询动态"
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "🤖 已就位"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 推送设置"},
                        "type": "primary",
                        "value": {"action": "settings"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📋 管理订阅"},
                        "type": "default",
                        "value": {"action": "subs:manage"},
                    },
                ],
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "💡 仅群主可管理订阅和推送设置。不需要的厂商请群主「退订」。",
                    }
                ],
            },
        ],
    }


# ========== RAG 智能问答卡片 ==========


def build_rag_answer_card(
    answer_text: str = "",
    sources: list[dict] | None = None,
    original_query: str = "",
) -> dict:
    """构建 RAG 智能问答的飞书 Interactive Card

    卡片布局：
      Header: 🤖 AI 行业情报
      Body:
        💬 你问：{original_query}
        ─────────
        {answer_text}（Markdown 渲染）
        ─────────
        📚 参考来源：
        [📖 标题一] → URL 按钮
        [📖 标题二] → URL 按钮
        ...

    Args:
        answer_text: LLM 生成的回答正文（Markdown 格式）
        sources: 引用来源列表 [{title, url, vendor, published_at}]
        original_query: 用户的原始问题

    Returns:
        飞书 Interactive Card JSON (dict)
    """
    if sources is None:
        sources = []

    elements = []

    # 用户问题回显
    if original_query:
        question_text = original_query[:200] + "..." if len(original_query) > 200 else original_query
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"💬 **你问：** {question_text}"},
        })
        elements.append({"tag": "hr"})

    # 答案正文
    answer_content = answer_text or "暂无答案"
    # 飞书卡片 lark_md 有 5000 字符限制，截断
    if len(answer_content) > 4800:
        answer_content = answer_content[:4800] + "\n\n...（内容过长已截断）"
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": answer_content},
    })

    # 引用来源
    if sources:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "📚 **参考来源：**"},
        })
        for i, src in enumerate(sources, 1):
            label_parts = []
            if src.get("vendor"):
                label_parts.append(src["vendor"])
            title_short = src.get("title", "查看原文")[:30]
            label_parts.append(title_short)
            label = f"📖 {' · '.join(label_parts)}"

            if src.get("url"):
                elements.append({
                    "tag": "action",
                    "actions": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": label[:40]},
                        "type": "default",
                        "url": src["url"],
                    }],
                })
            else:
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"  {i}. {' · '.join(label_parts)}"},
                })
    else:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": "💡 发送「OpenAI 最近有什么新闻」查看最新动态",
            }],
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "🤖 AI 行业情报"},
        },
        "elements": elements,
    }

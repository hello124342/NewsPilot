> **v1 已交付并超出范围。v2 新增：**
> - Twitter/X 监控（Nitter RSS 桥接）— 6 个 Twitter 源
> - 订阅/退订系统（6 厂商可选，@Bot 命令 + 卡片按钮）
> - 推送时间/频率自定义（3 档时间 × 3 种频率）
> - Chat 自动发现与生命周期管理（无需手动配置 chat_id）
> - 群主权限控制、Bot 加群/退群 Webhook
> - 双阶段推送（5am 抓取入库 + 9/12/18 按偏好投递）
>
> 最新架构见 [CLAUDE.md](./CLAUDE.md)，最新进展见 [implementation-plan.md](./implementation-plan.md)。

#### 1. 规格设计文档：`2026-08-07-feishu-ai-news-bot-design.md`

Markdown

```
# 飞书 AI 新闻推送 Bot (Feishu AI News Bot) 设计文档

- **日期**：2026-08-07
- **状态**：已批准 (Approved)
- **技术栈**：Python 3.10+ / FastAPI / LangGraph / LangChain / APScheduler / MySQL / Redis / 飞书 Open API

---

## 1. 项目概述 (Overview)

构建一个专用于飞书（Lark）平台的 AI 厂商新闻推送机器人。系统每天定时轮询主要 AI 厂商（OpenAI, DeepSeek, Kimi, Anthropic 等）的官方博客 RSS 订阅源，利用大模型（LLM）提炼核心要点，格式化为飞书富文本卡片（Interactive Card），批量推送到环境变量配置的固定飞书群/个人，并支持飞书用户在群内 `@Bot` 进行历史动态交互式查询。

> **一期范围（v1）**：仅支持 RSS 数据源。社交媒体（X/Twitter）和微信公众号动态监控列为后续版本。一期推送目标为固定 chat_id 列表（通过 `FEISHU_CHAT_IDS` 环境变量配置），不支持动态订阅/退订功能。

---

## 2. 系统整体架构与组件拆分 (Architecture & Components)
                              +-------------------+
                              | 外部数据源 (RSS/  |
                              | Twitter/公众号等)  |
                              +---------+---------+
                                        |
                                        v
```

+------------------+             +----------+----------+

|  飞书用户 / 群聊  |             |  定时任务 / 轮询调度 |

+--------+---------+             +----------+----------+

|                                  |

| (Webhook @Bot 消息)               | (发现新文章)

v                                  v

+--------+----------------------------------+----------+

|                   FastAPI Web 网关                   |

+--------+----------------------------------+----------+

|                                  |

| (用户查询意图)                    | (文章推送任务)

v                                  v

+--------+------------------+    +----------+----------+

|  LangGraph Agent 查询图   |    | LangGraph 总结推送图 |

| (Intent -> Query DB ->    |    | (Fetch -> LLM Summarize|

|  Formulate Card)          |    |  -> Formulate Card) |

+--------+------------------+    +----------+----------+

|                                  |

+----------------+-----------------+

|

v

+--------+--------+

|  飞书 Open API  | (发送卡片消息)

+-----------------+

```
### 核心组件说明

1. **FastAPI Web 网关 (`app/main.py`)**：
   - 暴露 `/feishu/events` 路由接收飞书 Event Webhook 回调（如 `@Bot` 消息事件）。
   - 实现飞书事件签名校验 (`security.py`) 与异步消息分发。
2. **定时轮询调度器 (`app/scheduler/jobs.py`)**：
   - 使用 `APScheduler` 定时拉取 RSS 数据源（每天 9:00）。
   - 查询 Redis Set 防重，避免重复推送。
3. **LangGraph 工作流引擎 (`app/graph/`)**：
   - `NewsPushGraph`：负责正文提取、LLM 总结提炼、存储与卡片推送。
   - `BotQueryGraph`：负责用户自然语言意图识别、数据库检索与飞书卡片回复。
4. **存储层 (`app/db/`)**：
   - **Redis**：URL 防重缓存、飞书 `tenant_access_token` 缓存。
   - **MySQL**：保存已处理的新闻元数据（标题、链接、来源厂商、发布时间、LLM 摘要要点）。
5. **LLM Provider 抽象层 (`app/llm/`)**：
   - 通过 Factory 模式统一封装多厂商 LLM 调用（OpenAI、Anthropic Claude、DeepSeek 等）。
   - 配置项 `LLM_PROVIDER` 指定当前使用的厂商，各厂商 API Key 独立配置。
   - 基于 LangChain BaseChatModel 统一接口，底层模型可插拔切换。

---

## 3. 数据流与 LangGraph 状态图 (Dataflow & LangGraph State)

### 3.1 新闻总结与推送图 (`NewsPushGraph`)
```

[Start] -> ExtractNode -> SummarizeNode -> StoreNode -> BuildCardNode -> SendFeishuNode -> [End]

```
* **ExtractNode 处理流程**：
  1. RSS Fetcher 从配置的 RSS 源列表拉取最近文章链接
  2. Redis 检查 URL 是否已处理（防重）
  3. 对新 URL，`web_scraper.py`（Trafilatura）抓取网页正文
  4. 输出 raw_content 和 title 到下游节点
* **批量处理**：每天一次轮询可能产生多篇新文章，调度器逐篇调用 Graph 处理，文章间错误隔离。

* **PushState 状态结构**：
  * `raw_url`: `str` — 原始新闻链接
  * `raw_content`: `str` — 抓取的正文内容
  * `vendor`: `str` — 识别出的厂商 (OpenAI, DeepSeek, Kimi 等)
  * `title`: `str` — 新闻标题
  * `summary_points`: `List[str]` — LLM 总结的 3 核心要点
  * `card_json`: `dict` — 飞书卡片 JSON
  * `status`: `str` — 执行结果 (`SUCCESS` / `FAILED`)

### 3.2 飞书交互查询图 (`BotQueryGraph`)
```

[Start Webhook] -> ParseIntentNode -> SearchDBNode -> FormatResponseNode -> ReplyFeishuNode -> [End]

```
* **QueryState 状态结构**：
  * `user_id` / `open_id`: `str` — 触发的用户 ID
  * `chat_id`: `str` — 群聊 / 单聊 ID
  * `user_query`: `str` — 用户输入的原始文本
  * `parsed_intent`: `dict` — 解析出的查询条件（例如：`{"vendor": "OpenAI", "days": 3}`）
  * `query_results`: `List[dict]` — 数据库查出的新闻列表
  * `reply_card_json`: `dict` — 最终回复的卡片 JSON

### 3.3 异常与重试逻辑 (Error Handling)
- **网页抓取**：采用 3 次指数退避重试，超时降级处理。
- **LLM API 调用**：基于 LangGraph 节点级别的 Retry 策略处理 429 Rate Limit。
- **飞书 Token**：自动在 Redis 中过期更新并续期。

---

## 4. 项目文件结构 (Directory Structure)

```text
lark-ai-news-bot/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 主入口与 API 路由
│   ├── core/                    # 配置与签名校验
│   │   ├── config.py            # pydantic-settings 配置项
│   │   └── security.py          # 飞书事件签名验证
│   ├── db/                      # 数据库层
│   │   ├── database.py          # SQLAlchemy Session 配置
│   │   ├── models.py            # MySQL ORM 映射
│   │   └── redis.py             # Redis 缓存与 URL 防重
│   ├── fetcher/                 # 数据源抓取
│   │   ├── rss_fetcher.py       # RSS 订阅
│   │   └── web_scraper.py       # 网页正文解析 (Trafilatura)
│   ├── graph/                   # LangGraph 工作流
│   │   ├── state.py             # State 状态定义
│   │   ├── news_push_graph.py   # 推送工作流定义
│   │   ├── bot_query_graph.py   # 查询工作流定义
│   │   └── nodes/               # 各节点实现
│   │       ├── extract.py
│   │       ├── summarize.py
│   │       ├── store.py
│   │       └── intent.py
│   ├── llm/                     # LLM Provider 抽象层
│   │   └── provider.py          # Factory: 统一封装 OpenAI/Anthropic/DeepSeek 等
│   ├── feishu/                  # 飞书 SDK 封装
│   │   ├── client.py            # 飞书消息发送 API
│   │   └── card_builder.py      # 富文本卡片生成逻辑
│   └── scheduler/               # 定时轮询调度
│       └── jobs.py
├── docs/                        # 设计与规格文档
│   └── superpowers/specs/2026-08-07-feishu-ai-news-bot-design.md
├── .env.example
├── docker-compose.yml           # FastAPI + MySQL + Redis Docker 编排
├── Dockerfile
└── requirements.txt
```

## 5. 飞书卡片消息样式规范 (Feishu Card Specification) — Plan A

交互式卡片 JSON 逻辑结构（实现于 `app/feishu/card_builder.py`）：

```
┌─────────────────────────────┐
│ Header (blue): 厂商名称      │  ← vendor name only
├─────────────────────────────┤
│ 📰 **Blog** · 2026-08-07    │  ← channel icon + channel type + date
│ ─────────────────────────── │
│ **文章标题**                  │  ← article title (bold)
│ ─────────────────────────── │
│ 💡 **核心要点总结**           │
│   1. 要点一                  │
│   2. 要点二                  │
│   3. 要点三                  │
│ ─────────────────────────── │
│ [📖 阅读原文]                │  ← primary button → raw_url
└─────────────────────────────┘
```

- **渠道图标**: 📰 Blog, 🐦 Twitter（定义在 `CHANNEL_ICON` dict）
- **函数签名**: `build_news_card(title, vendor, summary_points, raw_url, published_at, channel="Blog") -> dict`
- **channel 参数**: 控制渠道行显示（图标 + 渠道名）。默认 `"Blog"`




# Feishu AI News Bot

> FastAPI + LangGraph + Multi-Platform — AI 资讯追踪与交互助手（飞书 + Telegram）

## Features

- **10 数据源 / 6 厂商** — Blog RSS + Twitter (Nitter) 混合监控
- **LLM 智能摘要** — LangGraph StateGraph 管道，3 核心要点提炼
- **多平台推送** — 飞书 Interactive Card + Telegram Markdown/InlineKeyboard，统一 RichMessage 渲染
- **交互式 @Bot 查询** — 按厂商 + 日期 NL 检索历史 + RAG 智能问答（语义检索 + LLM 综合回答带引用）
- **订阅管理** — 厂商粒度的订阅/退订，群主/管理员权限控制
- **推送定制** — 3 时段 (09/12/18) × 3 频率 (每天/工作日/每周)
- **自动群发现** — 飞书 WebSocket + Telegram Webhook，入群即注册，无需手动配置 chat_id
- **RAG 智能问答** — ChromaDB 向量库 + OpenAI Embedding + LLM 综合回答，支持 "GPT-5 什么时候发布" 等自然语言问答
- **生产级可观测性** — 结构化 JSON 日志 + Prometheus 指标 + Grafana 仪表板（42 项监控指标）

## Architecture

```
FastAPI (HTTP)                WS Daemon Thread            APScheduler
/health /metrics              lark.ws.Client              05:00 process_rss
/admin/*                      (Feishu events)             09:00 deliver (multi-platform)
/webhook/telegram              │                           12:00 deliver (multi-platform)
  │                            │                           18:00 deliver (multi-platform)
  │                   ThreadPoolExecutor                      │
  │                    (max_workers=5)                        │
  │                       │                                  │
  └───────────────────────┼──────────────────────────────────┘
                          │
     Prometheus ◄─────────┤ (scrape /metrics every 15s)
         │                │
     Grafana ◄────────────┘ (dashboard at :3000)
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          MySQL        Redis       TTL Cache
       (persistent)  (dedup)    (thread-safe)
                          │
             ┌────────────┼──────────────┐
             ▼            ▼              ▼
       PlatformAdapter  LangGraph      Subscription
       (Feishu+Telegram)  Graphs         Handler
             │         (NewsPush +     (Facade → Repository)
             │          BotQuery)         
             │            │
             ▼            ▼
        RichMessage    ChromaDB
       (neutral fmt)  (vector store)
```

### Platform Adapter Layer

```
app/platforms/
├── adapter.py          PlatformAdapter ABC
├── message_model.py    RichMessage, ActionButton, etc.
├── registry.py         get_platform_adapter() factory
├── feishu/             FeishuAdapter + Card renderer
└── telegram/           TelegramAdapter + HTML/Keyboard renderer
```

### Design Patterns

| Pattern | Where | Purpose |
|---------|-------|---------|
| **Repository (ABC)** | `app/db/repositories.py` | 数据访问接口抽象，依赖倒置 |
| **Circuit Breaker** | `app/core/resilience.py` | 外部 API 熔断保护，三态机 |
| **Producer-Consumer** | `app/feishu/event_router.py` | 线程池解耦事件接收与处理 |
| **Factory** | `app/llm/provider.py` | 多 LLM 供应商统一创建 |
| **Factory** | `app/platforms/registry.py` | 多平台适配器统一创建 |
| **Adapter** | `app/platforms/` | 隔离 IM 平台差异（Feishu / Telegram） |
| **Strategy** | `app/fetcher/` | RSS vs HTML 抓取策略 |
| **Builder** | `app/feishu/card_builder.py` | 6 种飞书卡片构建 |
| **Observer** | `app/feishu/event_router.py` | SDK 事件分发 |
| **Facade** | `app/subscription/handler.py` | 兼容旧 API，委托 Repository |

See `docs/adr/` for Architecture Decision Records (9 ADRs) and `docs/concurrency-model.md` for the concurrency model.

### Pipeline

```
05:00 — process_rss_job()       fetch + summarize + store (no push)
09:00 — deliver_job("09:00")    query → 3-layer filter → send (multi-platform)
12:00 — deliver_job("12:00")    query → 3-layer filter → send (multi-platform)
18:00 — deliver_job("18:00")    query → 3-layer filter → send (multi-platform)

Filter layers: push_time → frequency → vendor subscription
```

## Project Structure

```
feishu-bot/
├── app/
│   ├── main.py                      # FastAPI entry, SOURCES, APScheduler, Telegram handlers
│   ├── core/
│   │   ├── config.py                # pydantic-settings (Feishu + Telegram + LLM + DB)
│   │   ├── cache.py                 # Thread-safe TTL memory cache
│   │   ├── resilience.py            # Circuit Breaker (三态机)
│   │   └── security.py              # [deprecated] Webhook signature
│   ├── platforms/                   # ★ Multi-Platform Adapter Layer
│   │   ├── adapter.py               # PlatformAdapter ABC
│   │   ├── message_model.py         # RichMessage, ActionButton, ConversationInfo
│   │   ├── registry.py              # Platform factory (get_platform_adapter)
│   │   ├── feishu/
│   │   │   ├── adapter.py           # FeishuAdapter
│   │   │   └── renderer.py          # RichMessage → Feishu Card JSON
│   │   └── telegram/
│   │       ├── adapter.py           # TelegramAdapter (python-telegram-bot)
│   │       ├── renderer.py          # RichMessage → HTML + InlineKeyboard
│   │       ├── webhook.py           # FastAPI webhook endpoint + my_chat_member
│   │       └── commands.py          # Command templates + vendor aliases
│   ├── db/
│   │   ├── database.py              # SQLAlchemy session factory + 12 auto-migrations
│   │   ├── models.py                # ORM: NewsArticle, Subscription*, ChatPreference*, ChatRegistry*
│   │   ├── redis.py                 # URL dedup + token cache
│   │   ├── repositories.py          # Repository ABC interfaces (platform-aware)
│   │   └── sql_repositories.py      # SQLAlchemy Repository implementations
│   ├── feishu/
│   │   ├── client.py                # Feishu Open API (send_card, get_chat_info)
│   │   ├── card_builder.py          # 7 card builders (news, welcome, settings, RAG answer)
│   │   ├── event_router.py          # WS event dispatch + ThreadPoolExecutor
│   │   └── ws_client.py             # WS daemon thread + isolated event loop
│   ├── fetcher/
│   │   ├── rss_fetcher.py           # RSS/Atom via feedparser
│   │   ├── kimi_scraper.py          # Kimi Blog HTML scraper
│   │   └── web_scraper.py           # Trafilatura full-text extraction
│   ├── graph/
│   │   ├── state.py                 # PushState + QueryState (platform-aware) TypedDict
│   │   ├── news_push_graph.py       # 5-node pipeline + conditional edges
│   │   ├── bot_query_graph.py       # conditional routing: list + qa paths (8 nodes)
│   │   └── nodes/                   # 13 node implementations (reply node platform-aware)
│   ├── llm/
│   │   └── provider.py              # Factory: OpenAI / Anthropic / DeepSeek
│   ├── rag/                         # RAG 模块 (向量检索 + 问答)
│   │   ├── embedder.py              # OpenAI text-embedding-3-small
│   │   └── vector_store.py          # ChromaDB PersistentClient CRUD
│   ├── prompts/
│   │   ├── loader.py                # YAML prompt loader
│   │   ├── intent.yaml
│   │   ├── summarize.yaml
│   │   └── rag_answer.yaml          # RAG 问答 prompt
│   ├── subscription/
│   │   └── handler.py               # Facade: commands + CRUD delegation (platform-aware)
│   └── chat/
│       └── lifecycle.py             # Facade: chat lifecycle + permissions (multi-platform)
├── docs/
│   ├── adr/                         # Architecture Decision Records (9)
│   │   ├── 0001-websocket-over-webhook.md
│   │   ├── 0002-two-phase-pipeline.md
│   │   ├── 0003-ws-daemon-thread.md
│   │   ├── 0004-langgraph-workflow.md
│   │   ├── 0005-soft-delete-subscriptions.md
│   │   ├── 0006-thread-pool-over-asyncio.md
│   │   ├── 0007-observability-stack.md
│   │   ├── 0008-rag-upgrade.md
│   │   └── 0009-multi-platform-adapter.md   # ★ NEW
│   └── concurrency-model.md         # Thread model + bottleneck analysis
├── tests/                           # 17 test files, 247 tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

> \* Tables with `platform` + `conversation_id` columns for multi-platform support.

## Quick Start

### 1. Environment

```bash
cp .env.example .env
```

```env
# 飞书（可选 — 未配置则禁用飞书）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx

# Telegram（可选 — 未配置则禁用 Telegram）
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234gh

# LLM
LLM_PROVIDER=openai          # openai | anthropic | deepseek
OPENAI_API_KEY=sk-xxx

# Database
MYSQL_HOST=localhost
REDIS_HOST=localhost
```

### 2. Docker

```bash
docker-compose up -d --build
```

### 3. Local Dev

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Usage

### 飞书

| Command | Example |
|---------|---------|
| Subscribe | `@Bot 订阅 OpenAI` |
| Unsubscribe | `@Bot 退订 Anthropic` |
| List | `@Bot 订阅列表` |
| Settings | `@Bot 设置` |
| Set Time | `@Bot 设置推送时间 晚上6点` |
| News Query | `@Bot OpenAI 最近有什么新闻` |
| RAG Q&A | `@Bot GPT-5 什么时候发布？` |

### Telegram

| Command | Example |
|---------|---------|
| Subscribe | `/subscribe OpenAI` |
| Unsubscribe | `/unsubscribe DeepSeek` |
| List | `/list` |
| Settings | `/settings` |
| Set Time | `/settime 18:00` |
| Set Frequency | `/setfrequency weekdays` |
| News Query | `OpenAI 最近有什么新闻` |
| RAG Q&A | `GPT-5 什么时候发布？` |
| Help | `/help` |

**Vendors:** OpenAI / Anthropic / Google DeepMind / DeepSeek / Kimi (Moonshot) / Z.ai (智谱)

**Push times:** 09:00 / 12:00 / 18:00 &nbsp;|&nbsp; **Frequencies:** daily / weekdays / weekly_monday

## Monitoring

`docker-compose up -d --build` 自带监控栈：

| 服务 | 地址 | 说明 |
|------|------|------|
| **Grafana** | `http://localhost:3000` (admin/admin) | 预制仪表板，8 行面板，实时刷新 |
| **Prometheus** | `http://localhost:9090` | 指标查询，15s 采集间隔 |
| **App Metrics** | `http://localhost:8000/metrics` | 原始 Prometheus 指标 |

仪表板覆盖：HTTP 流量、RSS 管道、推送投递、LLM 调用、WebSocket、飞书 API、Telegram API、内容抓取、熔断器。

## Testing

```bash
pytest -v          # 247 tests across 17 test files
```

## License

MIT

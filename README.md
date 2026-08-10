# Feishu AI News Bot

> FastAPI + LangGraph + 飞书 WebSocket — AI 资讯追踪与交互助手

## Features

- **10 数据源 / 6 厂商** — Blog RSS + Twitter (Nitter) 混合监控
- **LLM 智能摘要** — LangGraph StateGraph 管道，3 核心要点提炼
- **飞书 Interactive Card** — Plan A 风格卡片，按钮交互（订阅/设置/退订）
- **交互式 @Bot 查询** — 按厂商 + 日期 NL 检索历史
- **订阅管理** — 厂商粒度的订阅/退订，群主权限控制
- **推送定制** — 3 时段 (09/12/18) × 3 频率 (每天/工作日/每周)
- **自动群发现** — WebSocket 长连接，拉群即注册，无需配置 chat_id
- **生产级可观测性** — 结构化 JSON 日志 + Prometheus 指标 + Grafana 仪表板（36 项监控指标）

## Architecture

```
FastAPI (HTTP)                WS Daemon Thread            APScheduler
/health /metrics              lark.ws.Client              05:00 process_rss
/admin/*                       │ (receive events)          09:00 deliver
  │                             │                           12:00 deliver
  │                    ThreadPoolExecutor                   18:00 deliver
  │                     (max_workers=5)                        │
  │                        │                                  │
  └────────────────────────┼──────────────────────────────────┘
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
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         LangGraph     LangGraph    Subscription
        NewsPushGraph  BotQueryGraph   Handler
        (5 nodes)      (4 nodes)    (Facade → Repository)
```

### Design Patterns

| Pattern | Where | Purpose |
|---------|-------|---------|
| **Repository (ABC)** | `app/db/repositories.py` | 数据访问接口抽象，依赖倒置 |
| **Circuit Breaker** | `app/core/resilience.py` | 外部 API 熔断保护，三态机 |
| **Producer-Consumer** | `app/feishu/event_router.py` | 线程池解耦事件接收与处理 |
| **Factory** | `app/llm/provider.py` | 多 LLM 供应商统一创建 |
| **Strategy** | `app/fetcher/` | RSS vs HTML 抓取策略 |
| **Builder** | `app/feishu/card_builder.py` | 6 种飞书卡片构建 |
| **Observer** | `app/feishu/event_router.py` | SDK 事件分发 |
| **Facade** | `app/subscription/handler.py` | 兼容旧 API，委托 Repository |

See `docs/adr/` for Architecture Decision Records and `docs/concurrency-model.md` for the concurrency model.

### Pipeline

```
05:00 — process_rss_job()       fetch + summarize + store (no push)
09:00 — deliver_job("09:00")    query → 3-layer filter → send cards
12:00 — deliver_job("12:00")    query → 3-layer filter → send cards
18:00 — deliver_job("18:00")    query → 3-layer filter → send cards

Filter layers: push_time → frequency → vendor subscription
```

## Project Structure

```
feishu-bot/
├── app/
│   ├── main.py                     # FastAPI entry, SOURCES, APScheduler
│   ├── core/
│   │   ├── config.py               # pydantic-settings
│   │   ├── cache.py                # Thread-safe TTL memory cache
│   │   ├── resilience.py           # Circuit Breaker (三态机)
│   │   └── security.py             # [deprecated] Webhook signature
│   ├── db/
│   │   ├── database.py             # SQLAlchemy session factory
│   │   ├── models.py               # ORM: NewsArticle, Subscription, ChatPreference, ChatRegistry
│   │   ├── redis.py                # URL dedup + token cache
│   │   ├── repositories.py         # Repository ABC interfaces
│   │   └── sql_repositories.py     # SQLAlchemy Repository implementations
│   ├── feishu/
│   │   ├── client.py               # Feishu Open API (send_card, get_chat_info)
│   │   ├── card_builder.py         # 6 card builders (news, welcome, settings, etc.)
│   │   ├── event_router.py         # WS event dispatch + ThreadPoolExecutor
│   │   └── ws_client.py            # WS daemon thread + isolated event loop
│   ├── fetcher/
│   │   ├── rss_fetcher.py          # RSS/Atom via feedparser
│   │   ├── kimi_scraper.py         # Kimi Blog HTML scraper
│   │   └── web_scraper.py          # Trafilatura full-text extraction
│   ├── graph/
│   │   ├── state.py                # PushState + QueryState TypedDict
│   │   ├── news_push_graph.py      # 5-node pipeline + conditional edges
│   │   ├── bot_query_graph.py      # 4-node interactive pipeline
│   │   └── nodes/                  # 10 node implementations
│   ├── llm/
│   │   └── provider.py             # Factory: OpenAI / Anthropic / DeepSeek
│   ├── prompts/
│   │   ├── loader.py               # YAML prompt loader
│   │   ├── intent.yaml
│   │   └── summarize.yaml
│   ├── subscription/
│   │   └── handler.py              # Facade: commands + CRUD delegation
│   ├── chat/
│   │   └── lifecycle.py            # Facade: chat lifecycle + permissions
│   └── scheduler/
│       └── jobs.py
├── docs/
│   ├── adr/                        # Architecture Decision Records (6)
│   │   ├── 0001-websocket-over-webhook.md
│   │   ├── 0002-two-phase-pipeline.md
│   │   ├── 0003-ws-daemon-thread.md
│   │   ├── 0004-langgraph-workflow.md
│   │   ├── 0005-soft-delete-subscriptions.md
│   │   └── 0006-thread-pool-over-asyncio.md
│   └── concurrency-model.md        # Thread model + bottleneck analysis
├── tests/                          # 12 test files, 165 tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Quick Start

### 1. Environment

```bash
cp .env.example .env
```

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
LLM_PROVIDER=openai          # openai | anthropic | deepseek
OPENAI_API_KEY=sk-xxx
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

| Command | Example |
|---------|---------|
| Subscribe | `@Bot 订阅 OpenAI` |
| Unsubscribe | `@Bot 退订 Anthropic` |
| List | `@Bot 订阅列表` |
| Settings | `@Bot 设置` |
| Set Time | `@Bot 设置推送时间 晚上6点` |
| News Query | `@Bot OpenAI 最近有什么新闻` |

**Vendors:** OpenAI / Anthropic / Google DeepMind / DeepSeek / Kimi (Moonshot) / Z.ai

## Monitoring

`docker-compose up -d --build` 自带监控栈：

| 服务 | 地址 | 说明 |
|------|------|------|
| **Grafana** | `http://localhost:3000` (admin/admin) | 预制仪表板，8 行面板，实时刷新 |
| **Prometheus** | `http://localhost:9090` | 指标查询，15s 采集间隔 |
| **App Metrics** | `http://localhost:8000/metrics` | 原始 Prometheus 指标 |

仪表板覆盖：HTTP 流量、RSS 管道、推送投递、LLM 调用、WebSocket、飞书 API、内容抓取、熔断器。

## Testing

```bash
pytest -v          # ~190 tests across 15 test files
```

## License

MIT

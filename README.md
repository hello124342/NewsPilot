<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="AI News Bot" src="https://img.shields.io/badge/🤖-AI%20News%20Bot-blue?style=for-the-badge" width="320">
  </picture>
</p>

<p align="center">
  <strong>Multi-Platform AI News Aggregator & Intelligent Q&A Bot</strong>
  <br>
  多平台 AI 资讯聚合 · 智能问答机器人 · <b>Feishu</b> + <b>Telegram</b> + <b>Discord</b>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688.svg" alt="FastAPI"></a>
  <a href="https://github.com/hello124342/aiNewBot/actions"><img src="https://img.shields.io/badge/tests-355%20passed-brightgreen.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
  <a href="https://github.com/hello124342/aiNewBot"><img src="https://img.shields.io/badge/platform-Feishu_%7C_Telegram_%7C_Discord-5865F2.svg" alt="Platforms"></a>
  <a href="#"><img src="https://img.shields.io/badge/coverage-57%25-yellow.svg" alt="Coverage"></a>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#monitoring">Monitoring</a> ·
  <a href="#documentation">Docs</a>
</p>

---

## ✨ Features

<table>
<tr><td width="50%">

### 🚀 Core
- **10 sources × 6 vendors** — Blog RSS + Twitter (Nitter bridge)
- **LLM Summarization** — 3 key points per article via LangGraph pipeline
- **Multi-Platform** — Feishu Interactive Cards + Telegram Markdown

### 🧠 AI Intelligence
- **Intent Router** — auto-classify queries as list search vs. RAG Q&A
- **Semantic Search** — ChromaDB + OpenAI `text-embedding-3-small`
- **Cited Answers** — LLM synthesizes answers with `[来源 N]` citations

</td><td width="50%">

### 🔔 Delivery
- **3 push windows daily** — 09:00 · 12:00 · 18:00
- **3 frequencies** — daily · weekdays · weekly digest
- **Per-vendor subscriptions** — granular control per chat

### 📊 Production Ready
- **Grafana dashboard** — 10-row pre-built monitoring
- **Prometheus metrics** — 43 metric families (incl. query-pool saturation, cache hit rate, queue backlog)
- **Structured logging** — JSON format, configurable `LOG_LEVEL`
- **Service governance** — LLM circuit breaker + keyword/list degradation + token-bucket rate limit
- **Multi-level cache** — L1 LRU + L2 Redis (singleflight · null-cache · TTL jitter)
- **Redis Stream queue** — at-least-once push delivery + XAUTOCLAIM retry + DLQ + idempotency lock
- **Async execution** — feature-flagged asyncio pool (thread → coroutine, 9.7× throughput on IO-bound load)
- **355 tests** — 22 test files, TDD workflow

</td></tr>
</table>

---

## 🏗 Architecture

```
                   ┌──────────────────────────────────┐
                   │        FastAPI Application        │
                   │   /health  /metrics  /admin/*     │
                   │   /webhook/telegram               │
                   └──────────┬───────────────────────┘
                              │
     ┌────────────────────────┼────────────────────────┐
     │                        │                        │
     ▼                        ▼                        ▼
┌─────────┐           ┌──────────────┐          ┌──────────┐
│ Feishu  │           │   Telegram   │          │ Scheduler│
│ WS (SDK)│           │   Webhook    │          │ 05:00    │
│ daemon  │           │   (FastAPI)  │          │ 09/12/18 │
│ thread  │           │              │          └──────────┘
└────┬────┘           └──────┬───────┘
     │                       │
     └───────────┬───────────┘
                 │  ThreadPoolExecutor (5 workers)
                 ▼
     ┌───────────────────────────┐
     │   Platform Adapter Layer  │
     │  RichMessage (neutral)    │
     │  ┌──────────┬──────────┐  │
     │  │ Feishu   │ Telegram │  │
     │  │ Adapter  │ Adapter  │  │
     │  └──────────┴──────────┘  │
     └───────────┬───────────────┘
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
┌────────┐ ┌─────────┐ ┌─────────┐
│ MySQL  │ │ Redis   │ │TTL Cache│
└────────┘ └─────────┘ └─────────┘
     │
     ▼
┌─────────────────────────────────┐
│        LangGraph Workflows       │
│  NewsPushGraph (5 nodes)        │
│  BotQueryGraph (8 nodes, 2 paths)│
└─────────────┬───────────────────┘
              │
     ┌────────┼────────┐
     ▼        ▼        ▼
  OpenAI  Anthropic  DeepSeek       ChromaDB
  (LLM)                          (Vector Store)
```

### 🔌 Platform Adapter Pattern

```python
from app.platforms.registry import get_platform_adapter

adapter = get_platform_adapter("telegram", settings)
adapter.send_message(chat_id, RichMessage(
    title="🤖 OpenAI",
    body="**GPT-5 Released**\n\n1. Point one\n2. Point two",
    buttons=[ActionButton(label="📖 Read More", action="url", value=url)],
))
```

| Adapter | Message Format | Event Transport |
|---------|---------------|-----------------|
| `FeishuAdapter` | Interactive Card JSON (`lark_md`) | WebSocket (lark-oapi SDK) |
| `TelegramAdapter` | HTML + InlineKeyboardMarkup | Webhook (FastAPI route) |
| `DiscordAdapter` | Embed JSON + Button components | Gateway (discord.py, daemon thread) |
| *Slack (planned)* | Block Kit JSON | Socket Mode |

### 🧵 Design Patterns

| Pattern | Location | Why |
|---------|----------|-----|
| **Repository (ABC)** | `db/repositories.py` | DIP — business logic depends on interfaces |
| **Circuit Breaker** | `core/resilience.py` | Feishu API fault tolerance (CLOSED→OPEN→HALF_OPEN) |
| **Producer-Consumer** | `feishu/event_router.py` | ThreadPool decouples event receive from processing |
| **Factory ×2** | `llm/provider.py` · `platforms/registry.py` | Multi-provider LLM + multi-platform adapter creation |
| **Adapter** | `platforms/` | Isolates platform-specific rendering & transport |
| **Strategy** | `fetcher/` · `graph/nodes/intent_router.py` | RSS/Kimi scraper · list/qa routing |
| **Builder** | `feishu/card_builder.py` | 7 card type builders |
| **Observer** | `feishu/event_router.py` | SDK `EventDispatcherHandler` |
| **Facade** | `subscription/handler.py` · `chat/lifecycle.py` | Backward-compat delegation to Repository |
| **Decorator** | `core/metrics.py` | `@track_llm_call` · `@track_feishu_api` |

### ⚡ Concurrency & Reliability

An enterprise-grade concurrency stack layered on top of the business logic — every piece is feature-flagged or degrades gracefully, so the bot stays up when a dependency doesn't.

```
        submit(query)                          deliver_job (push)
             │                                        │
   ┌─────────▼──────────┐                   ┌─────────▼──────────┐
   │  Query Executor    │                   │  Redis Stream Queue │
   │  thread │ async ◄── QUERY_EXECUTOR_MODE │  XADD → XREADGROUP  │
   │  token-bucket RL   │                   │  XACK / XAUTOCLAIM  │
   └─────────┬──────────┘                   │  DLQ + idempotency  │
             │                              └─────────┬──────────┘
   ┌─────────▼──────────┐                   ┌─────────▼──────────┐
   │  Multi-Level Cache │                   │  N consumer threads │
   │  L1 LRU → L2 Redis │                   │  render → send → ACK│
   │  singleflight/jitter│                  └────────────────────┘
   └─────────┬──────────┘
   ┌─────────▼──────────┐
   │  LLM Circuit Breaker│  OPEN → fast-fail <1ms → keyword/list degradation
   └────────────────────┘
```

| Concern | Mechanism | Key file | ADR |
|---------|-----------|----------|-----|
| **Throughput** | Feature-flagged asyncio coroutine pool (`QUERY_EXECUTOR_MODE=thread\|async`), `Semaphore(100)` backpressure — transparent to platform layer via submit-delegate | `core/async_query_executor.py` | [0013](./docs/adr/0013-async-query-pipeline.md) |
| **Reliable delivery** | Redis Stream, at-least-once, XAUTOCLAIM retry, DLQ after 3 tries, idempotency lock (`SET NX EX`); inline fallback when Redis down | `queue/stream_queue.py` · `queue/deliver_consumer.py` | [0011](./docs/adr/0011-redis-stream-delivery-queue.md) |
| **Cost / latency** | L1 LRU + L2 Redis, singleflight (breakdown), null-cache (penetration), ±10% TTL jitter (avalanche) | `core/multi_cache.py` | [0010](./docs/adr/0010-multi-level-cache.md) |
| **Fault tolerance** | LLM circuit breaker (5-fail → OPEN → fast-fail), keyword/article-list degradation | `llm/provider.py` · `graph/nodes/` | [0012](./docs/adr/0012-llm-circuit-breaker-and-degradation.md) |
| **Overload** | Per-user token bucket (`burst=3`, `refill=0.5/s`) — tolerates bursts, throttles spam | `core/query_executor.py` | — |

> **Benchmark:** `python scripts/benchmark_query.py` — IO-bound load, thread QPS 20 → async QPS 195 (**9.7×**). Full results in [docs/benchmark-results.md](./docs/benchmark-results.md).

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- MySQL 8.0+ & Redis 6.0+
- Feishu App credentials ([Open Platform](https://open.feishu.cn/)) · Telegram Bot Token ([@BotFather](https://t.me/BotFather)) · or Discord Bot ([Developer Portal](https://discord.com/developers/applications))

### 1. Clone & Configure

```bash
git clone https://github.com/hello124342/aiNewBot.git
cd aiNewBot
cp .env.example .env
```

```env
# ── Platform (at least one required) ──
FEISHU_APP_ID=cli_xxx              # 飞书 App ID
FEISHU_APP_SECRET=xxx              # 飞书 App Secret

TELEGRAM_BOT_TOKEN=123456:ABCdef   # Telegram Bot Token

DISCORD_BOT_TOKEN=xxx               # Discord Bot Token (enable Message Content Intent)

# ── LLM ──
LLM_PROVIDER=openai                # openai | anthropic | deepseek
OPENAI_API_KEY=sk-xxx
LLM_TIMEOUT_SECONDS=60             # 单次调用超时，防止挂起的调用占满查询池

# ── Admin API ──
ADMIN_API_TOKEN=                   # /admin/* 访问令牌，留空则管理端点禁用

# ── Database ──
MYSQL_HOST=localhost
REDIS_HOST=localhost
```

> **Note:** All three platforms are optional — the bot works with any subset. Unconfigured platforms silently disable.

> **Security:** `/admin/*` endpoints can trigger mass delivery and batch embedding billing. They require an `X-Admin-Token` header matching `ADMIN_API_TOKEN`, and are **disabled entirely** (HTTP 503) when that value is unset. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 2. Docker (Recommended)

```bash
docker-compose up -d --build
# Starts: app + MySQL + Redis + Prometheus + Grafana
```

### 3. Local Development

```bash
pip install -r requirements.txt          # floor constraints (>=)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

For reproducible/production installs use the committed lock file instead:

```bash
pip install -r requirements-lock.txt     # exact pins
```

> **Regenerating the lock file:** run `pip freeze` **inside the project venv**, not a conda base environment — conda emits `@ file:///...` local build paths that fail to install anywhere else. See [CLAUDE.md](./CLAUDE.md#commands).

### 4. Verify

```bash
curl http://localhost:8000/health          # → {"status": "ok"}
curl http://localhost:8000/metrics         # → Prometheus metrics
pytest -v                                  # → 355 passed
```

---

## 💬 Usage

### Feishu (飞书)

| Action | Example |
|--------|---------|
| Subscribe | `@Bot 订阅 OpenAI` |
| Unsubscribe | `@Bot 退订 Anthropic` |
| List subscriptions | `@Bot 订阅列表` |
| Push settings | `@Bot 设置` |
| Set push time | `@Bot 设置推送时间 晚上6点` |
| Set frequency | `@Bot 设置频率 仅工作日` |
| News search | `@Bot OpenAI 最近有什么新闻` |
| RAG Q&A | `@Bot GPT-5 什么时候发布？` |

### Telegram

| Action | Command |
|--------|---------|
| Start | `/start` |
| Subscribe | `/subscribe OpenAI` or `/subscribe all` |
| Unsubscribe | `/unsubscribe DeepSeek` |
| List subscriptions | `/list` |
| Push settings | `/settings` |
| Set push time | `/settime 18:00` |
| Set frequency | `/setfrequency weekdays` |
| Help | `/help` |
| News search | `OpenAI 最近有什么新闻` (plain text) |
| RAG Q&A | `GPT-5 什么时候发布？` (plain text) |

### Discord

Messages must **@mention the bot** to be processed.

| Action | Example |
|--------|---------|
| Welcome / Help | `@Bot 开始` or `@Bot 帮助` |
| Subscribe | `@Bot 订阅 OpenAI` or `@Bot 订阅所有` |
| Unsubscribe | `@Bot 退订 DeepSeek` |
| List subscriptions | `@Bot 订阅列表` |
| Push settings | `@Bot 设置` |
| Set push time | `@Bot 设置推送时间 18:00` |
| Set frequency | `@Bot 设置推送频率 工作日` |
| News search | `@Bot OpenAI 最近有什么新闻` |
| RAG Q&A | `@Bot GPT-5 什么时候发布？` |

> **Setup note:** enable the **Message Content Intent** in Discord Developer Portal (Bot → Privileged Gateway Intents) — required for the bot to read @mention text.

> **Zero config:** you never fill in channel IDs. Inviting the bot to a server registers a default channel (`system_channel`, else the first channel it can post in) and auto-subscribes all vendors; `@`-mentioning it in any other channel registers that channel on the spot. `DISCORD_GUILD_ID` is optional and only limits onboarding to one server. The same holds for Feishu and Telegram — all conversation IDs are discovered at runtime.

### Supported Vendors

`OpenAI` · `Anthropic` · `Google DeepMind` · `DeepSeek` · `Kimi (Moonshot)` · `Z.ai / 智谱`

### Push Settings

| Setting | Values |
|---------|--------|
| **Time** | `09:00` · `12:00` · `18:00` |
| **Frequency** | `daily` · `weekdays` · `weekly_monday` |

---

## 📊 Monitoring

The `docker-compose` stack includes a full observability pipeline:

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | `admin` / `admin` |
| **Prometheus** | http://localhost:9090 | — |
| **App Metrics** | http://localhost:8000/metrics | — |

**Dashboard panels:** KPI overview · HTTP traffic · RSS pipeline · Push delivery · LLM calls · Feishu API · WebSocket status · Platform breakdown · **Cache & governance** (hit rate · degradation · query-pool saturation) · **Redis Stream queue** (backlog · enqueue vs consume · retry/DLQ/fallback)

---

## 📁 Project Structure

```
aiNewBot/
├── app/
│   ├── main.py                  # FastAPI entry, scheduler, Telegram handlers
│   ├── core/                    # config, cache, resilience, logging, metrics
│   ├── platforms/               # ★ Multi-platform adapter layer
│   │   ├── adapter.py           #   PlatformAdapter ABC
│   │   ├── message_model.py     #   RichMessage, ActionButton, etc.
│   │   ├── registry.py          #   get_platform_adapter() factory
│   │   ├── feishu/              #   FeishuAdapter + Card renderer
│   │   ├── telegram/            #   TelegramAdapter + webhook + commands
│   │   └── discord/             #   DiscordAdapter + renderer + gateway
│   ├── db/                      # SQLAlchemy models, Redis, Repository ABC+impl
│   ├── feishu/                  # Feishu SDK client, card builders, WS event router
│   ├── fetcher/                 # RSS parser, Kimi scraper, Trafilatura extractor
│   ├── graph/                   # LangGraph: 2 graphs, 13 nodes, state definitions
│   ├── llm/                     # Multi-provider LLM factory + circuit breaker
│   ├── queue/                   # ★ Redis Stream delivery queue + consumer pool
│   ├── rag/                     # ChromaDB vector store + OpenAI embeddings
│   ├── subscription/            # Subscription commands & domain logic
│   └── chat/                    # Chat lifecycle & permissions
├── docs/
│   ├── adr/                     # 13 Architecture Decision Records
│   ├── benchmark-results.md     # thread vs async throughput benchmark
│   └── concurrency-model.md     # Thread model analysis
├── scripts/                     # benchmark_query.py (thread vs async)
├── tests/                       # 355 tests / 22 test files
├── monitoring/                  # Prometheus & Grafana configs
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-lock.txt        # exact pins (committed, for production)
└── CLAUDE.md                    # AI coding guide
```

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [CLAUDE.md](./CLAUDE.md) | Comprehensive codebase guide for AI coding assistants |
| [ADR 0001](./docs/adr/0001-websocket-over-webhook.md) | Why WebSocket over HTTP webhook |
| [ADR 0002](./docs/adr/0002-two-phase-pipeline.md) | Why two-phase pipeline (fetch → deliver) |
| [ADR 0007](./docs/adr/0007-observability-stack.md) | Observability design rationale |
| [ADR 0008](./docs/adr/0008-rag-upgrade.md) | RAG upgrade decision |
| [ADR 0009](./docs/adr/0009-multi-platform-adapter.md) | Multi-platform adapter pattern |
| [ADR 0010](./docs/adr/0010-multi-level-cache.md) | Multi-level cache: two levels + three guards |
| [ADR 0011](./docs/adr/0011-redis-stream-delivery-queue.md) | Redis Stream delivery queue (queue on push, not query) |
| [ADR 0012](./docs/adr/0012-llm-circuit-breaker-and-degradation.md) | LLM circuit breaker + degradation |
| [ADR 0013](./docs/adr/0013-async-query-pipeline.md) | Async query pipeline (thread → asyncio, feature flag) |
| [Concurrency Model](./docs/concurrency-model.md) | Thread model with bottleneck analysis |
| [Benchmark Results](./docs/benchmark-results.md) | thread vs async throughput (9.7×) |
| [Implementation Plan](./implementation-plan.md) | TDD task breakdown (v1–v3) |

---

## 🧪 Testing

```bash
pytest -v              # Full suite: 355 tests across 22 files
pytest -v --tb=short   # Compact tracebacks
pytest -v -k "telegram" # Run Telegram-specific tests only
pytest -v -k "discord"  # Run Discord-specific tests only
```

---

## 📄 License

MIT © 2026 — see [LICENSE](./LICENSE) for details.

---

<p align="center">
  <sub>Built with ❤️ using FastAPI · LangGraph · SQLAlchemy · lark-oapi · python-telegram-bot · ChromaDB</sub>
</p>

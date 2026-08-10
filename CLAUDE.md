# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Feishu AI News Bot — a FastAPI + LangGraph service that polls AI vendor news sources (Blog RSS + Twitter via Nitter) daily, summarizes articles via LLM, and pushes Interactive Cards to Feishu (Lark). Supports @Bot queries from within Feishu groups, with RAG-powered semantic search and AI-assisted Q&A.

**Features:** 10 sources across 6 vendors (Blog RSS + Twitter via Nitter). Dynamic chat discovery via Feishu WebSocket long connection (no hardcoded chat IDs). Per-chat vendor subscriptions with push time & frequency customization. Group owner permission control. Multi-LLM support (OpenAI / Anthropic / DeepSeek). RAG intelligent Q&A — semantic search (ChromaDB + OpenAI embeddings) with LLM-generated answers with citations. Intent routing: auto-classifies queries as "list search" vs "natural language Q&A".

**Scheduling:** Articles are fetched and summarized at 5:00 AM daily, then delivered at 9:00 / 12:00 / 18:00 based on each chat's time preference. Multi-layer push filtering: push_time → frequency (daily/weekdays/weekly) → vendor subscription.

**Event receiving:** WebSocket long connection via `lark-oapi` SDK — no public URL or webhook needed. Events handled in a daemon thread with independent asyncio event loop. FastAPI serves `/health`, `/metrics`, and `/admin/*` endpoints.

**Observability:** Structured JSON logging (`python-json-logger`), Prometheus metrics (42 counters/gauges/histograms across HTTP, LLM, Feishu API, Pipeline, CircuitBreaker, WebSocket, RAG), Grafana dashboard (8-row pre-built dashboard). Full monitoring stack via `docker-compose` (Prometheus + Grafana).

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, LangChain Core, Pydantic v2, SQLAlchemy, Redis-py, APScheduler, lark-oapi, Trafilatura, feedparser, httpx, ChromaDB, OpenAI embeddings, Pytest, Docker, Prometheus, Grafana.

**Code style:** Comments in Chinese, variable/function/class names in English.

**Design Patterns:**

| Pattern | Location | Type |
|---------|----------|------|
| **Repository (ABC)** | `app/db/repositories.py` → `sql_repositories.py` | Data access abstraction; `handler.py`/`lifecycle.py` are facades |
| **Circuit Breaker** | `app/core/resilience.py` | 3-state machine (CLOSED/OPEN/HALF_OPEN) for Feishu API calls |
| **Producer-Consumer** | `app/feishu/event_router.py` | `ThreadPoolExecutor(max_workers=5)` offloads event processing from WS thread |
| **Factory** | `app/llm/provider.py` | `get_llm()` returns provider-specific `BaseChatModel` |
| **Strategy** | `app/fetcher/` | RSS vs Kimi HTML scraper selected by `source["fetcher"]` |
| **Builder** | `app/feishu/card_builder.py` | 7 card type builders (news, RAG answer, subscription, welcome, settings) |
| **Observer** | `app/feishu/event_router.py` | SDK `EventDispatcherHandler` routes events to typed handlers |
| **Facade** | `app/subscription/handler.py`, `app/chat/lifecycle.py` | Module-level functions delegate to Repository, preserving backward compat |
| **Decorator** | `app/core/metrics.py` | `@track_llm_call`, `@track_feishu_api`, `@track_job_metrics` — non-invasive metric instrumentation |
| **Registry (isolated)** | `app/core/metrics.py` | `CollectorRegistry()` singleton — metrics isolated from global Prometheus registry |
| **Strategy (intent routing)** | `app/graph/nodes/intent_router.py` | LLM classification + keyword heuristic fallback: list vs qa routing |

**Concurrency Model:** 3-thread architecture — FastAPI main (uvicorn asyncio), WS daemon (isolated event loop), Event worker pool (ThreadPoolExecutor). Shared state via MySQL/Redis/TTL Cache (threading.Lock). See `docs/concurrency-model.md` for full analysis.

**Architecture Decisions:** 8 ADRs in `docs/adr/` covering WebSocket choice, two-phase pipeline, WS thread isolation, LangGraph workflow, soft-delete, thread-pool-over-asyncio, observability stack, and RAG upgrade.

**Database Migrations:** Lightweight auto-migration in `app/db/database.py:_run_migrations()` — detects missing columns on startup and applies ALTER TABLE. Safe, idempotent, no external migration framework needed.

## Commands

```bash
# Install
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Run (requires MySQL + Redis)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Docker
docker-compose up -d --build

# Tests
pytest -v
```

## Architecture

### Scheduling (Two-Phase Pipeline)

```
05:00 — process_rss_job()  → fetch + summarize + store (no push)
09:00 — deliver_job("09:00") → query today's articles → filter → send cards
12:00 — deliver_job("12:00") → query today's articles → filter → send cards
18:00 — deliver_job("18:00") → query today's articles → filter → send cards
```

The 5 AM batch uses `build_push_graph(push_enabled=False)` — it only runs extract→summarize→store, skipping card building and sending. Delivery jobs query stored articles and apply three-layer filtering: push_time preference, frequency setting, and vendor subscription.

### WebSocket Event Flow

Events arrive via `lark-oapi` WebSocket long connection (no HTTP webhook):

```
飞书服务器 ←→ lark.ws.Client (daemon thread, independent event loop)
                        │
                        ▼
              lark.EventDispatcherHandler
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  handle_message   handle_bot_added  handle_bot_removed
  (text + cards)   (group onboard)   (deactivate)
        │
        ├── subscription command? → _dispatch_subscription_command()
        └── news query?          → BotQueryGraph
```

- `app/feishu/ws_client.py` — daemon thread, independent `asyncio` loop (patches SDK module-level loop), auto-reconnect (exponential backoff 1s→60s)
- `app/feishu/event_router.py` — typed SDK handlers → existing business logic, message dedup via OrderedDict cache

### LangGraph Workflows

Two LangGraph workflows drive the system:

1. **NewsPushGraph** (`app/graph/news_push_graph.py`) — periodic pipeline:
   `[Start] → ExtractNode → SummarizeNode → StoreNode → BuildCardNode → SendFeishuNode → [End]`
   - `extract.py` — fetches raw article content via Trafilatura
   - `summarize.py` — LLM distills 3 key points (with 3x retry)
   - `store.py` — persists to MySQL using state's `published_at` field
   - `build_card.py` — builds Interactive Card JSON from state fields
   - `send_feishu.py` — resolves dynamic targets via chat_registry + subscription filter, sends cards
   - Supports checkpointing (`enable_checkpoint`) and human-in-the-loop (`enable_human_review`)
   - Conditional edges: FAILED status routes directly to END

2. **BotQueryGraph** (`app/graph/bot_query_graph.py`) — interactive pipeline with conditional routing:
   ```
   [Start] → IntentRouterNode
                ├─ "list" → IntentNode → SearchDBNode → FormatResponseNode → ReplyFeishuNode → [End]
                └─ "qa"   → RAGRetrieveNode → RAGAnswerNode → FormatRAGResponseNode → ReplyFeishuNode → [End]
   ```
   - `intent_router.py` — **NEW** — LLM 3-class classification (list/qa/command) with 2x retry + keyword heuristic fallback
   - `intent.py` — parses user NL into structured query (vendor via alias map, date range `_extract_days_from_query()`), LLM with 3x retry + keyword fallback
   - `search_db.py` — queries MySQL for matching articles by vendor + calendar day range (`_calc_since()` uses 00:00 UTC boundaries)
   - `rag_retrieve.py` — **NEW** — semantic search: query embedding → ChromaDB top-K → MySQL backfill `raw_content`
   - `rag_answer.py` — **NEW** — LLM reads context + user question → comprehensive answer with `[来源 N]` citations
   - `format_response.py` — builds multi-article result card (or "未找到" empty card)
   - `format_rag_response.py` — **NEW** — wraps RAG answer into `build_rag_answer_card()`
   - `reply_feishu.py` — sends reply card to chat_id from QueryState

### Key Modules

**Application Layer:**
- `app/main.py` — FastAPI entry point, SOURCES config, APScheduler lifecycle (4 jobs), `/health`, `/metrics`, `/admin/*` endpoints
- `app/core/config.py` — all config via `pydantic-settings` (env vars): Feishu, LLM, MySQL, Redis, `LOG_LEVEL`
- `app/core/logging_config.py` — structured JSON logging via `python-json-logger`, configurable `LOG_LEVEL`, suppresses noisy third-party libs
- `app/core/metrics.py` — 36 Prometheus metrics (counters/gauges/histograms), decorator/context-manager instrumentation, isolated `CollectorRegistry`
- `app/core/security.py` — [已废弃] Feishu webhook 签名验证，WebSocket 模式无需验签

**Feishu Integration:**
- `app/feishu/client.py` — Feishu Open API via `lark-oapi` SDK, auto-managed token
- `app/feishu/card_builder.py` — card builders: `build_news_card()` (Plan A), `build_rag_answer_card()` (RAG Q&A), `build_subscription_reply()`, `build_subscription_list_card()`, `build_welcome_card()`, `build_group_welcome_card()`, `build_settings_card()`
- `app/feishu/event_router.py` — WebSocket event dispatcher: typed SDK handlers for messages, card actions, bot lifecycle events. Events dispatched via `ThreadPoolExecutor`. Fixed first-message bug (welcome card no longer blocks query processing)
- `app/feishu/ws_client.py` — WS thread manager: independent event loop, auto-reconnect with exponential backoff, daemon thread for FastAPI coexistence

**RAG (Retrieval-Augmented Generation):**
- `app/rag/embedder.py` — OpenAI `text-embedding-3-small` (1536-dim) via `openai.OpenAI` client, 3x retry, Prometheus metrics
- `app/rag/vector_store.py` — ChromaDB `PersistentClient` (local `./chroma_data/`), CRUD operations: `add_article()`, `search()`, `delete_article()`, `collection_count()`
- `app/prompts/rag_answer.yaml` — RAG answer prompt with citation rules

**Data Fetching:**
- `app/fetcher/rss_fetcher.py` — RSS/Atom parsing via feedparser, `detect_vendor()` utility
- `app/fetcher/kimi_scraper.py` — Kimi Blog HTML scraper (no RSS feed available)
- `app/fetcher/web_scraper.py` — article full-text extraction via Trafilatura (3x exponential backoff)

**Storage:**
- `app/db/models.py` — SQLAlchemy ORM: `NewsArticle` (with `raw_content` column for RAG), `Subscription`, `ChatPreference`, `ChatRegistry`
- `app/db/database.py` — SQLAlchemy session factory + `_run_migrations()` auto-migration for missing columns
- `app/db/redis.py` — URL dedup cache (Redis Set) + `tenant_access_token` cache
- `app/db/repositories.py` — Repository ABC interfaces (`SubscriptionRepository`, `ChatRegistryRepository`)
- `app/db/sql_repositories.py` — SQLAlchemy Repository implementations; `replace_repos()` for test injection
- `app/core/cache.py` — Thread-safe TTL memory cache (300s). Caches chat_type, owner_id, preferences
- `app/core/resilience.py` — Circuit Breaker (3-state: CLOSED/OPEN/HALF_OPEN). Injected into `FeishuClient`

**LLM:**
- `app/llm/provider.py` — Factory for multi-provider LLM (OpenAI/Anthropic/DeepSeek), returns `BaseChatModel`
- `app/prompts/loader.py` — YAML-based prompt template loader (`intent.yaml`, `summarize.yaml`, `rag_answer.yaml`)

**Subscription & Chat Management:**
- `app/subscription/handler.py` — subscribe/unsubscribe/list commands, vendor aliases (12+ mappings), command regex detection, push time/frequency preferences. Constant: `ALL_VENDORS`, `PUSH_TIMES` (09:00/12:00/18:00), `FREQUENCIES` (daily/weekdays/weekly_monday)
- `app/chat/lifecycle.py` — chat auto-registration on bot added, deactivation on bot removed, active chat queries, owner_id caching, permission check (`can_manage_subscription()`)

**State definitions** are in `app/graph/state.py` — `PushState` (raw_url, raw_content, vendor, title, published_at, summary_points, card_json, status) and `QueryState` (user_id, chat_id, user_query, parsed_intent, query_results, query_type, rag_context, rag_answer, reply_card_json) TypedDicts.

**Error handling:** 3x exponential backoff on scraping, LangGraph node-level retry for 429 Rate Limit, auto-refreshing Feishu token in Redis. URL marked processed only after successful card send (or when zero subscribers, to prevent infinite reprocessing). RAG embedding failures are logged but don't block the main news processing pipeline.

### RAG Query Flow

```
User: "GPT-5 什么时候发布？"
       │
       ▼
intent_router: ──"qa"──→ rag_retrieve               ┌──────────────────────┐
                            │                         │ ChromaDB             │
                            ├─ get_embedding(query)   │ (PersistentClient)   │
                            ├─ search(top_k=5) ──────→│ ./chroma_data/       │
                            ├─ _backfill_raw_content  │ 1536-dim vectors     │
                            │  (MySQL JOIN)           └──────────────────────┘
                            ▼
                          rag_answer
                            ├─ _build_context_text(context)
                            ├─ LLM reads context + question
                            ├─ _extract_sources(context)
                            ▼
                          format_rag_response → reply_feishu
                            │
                            ▼
                    ┌─────────────────────────────┐
                    │ 🤖 AI 行业情报 (green)       │
                    │ 💬 GPT-5 什么时候发布？      │
                    │ ...LLM answer...             │
                    │ 📚 [来源1] [来源2] [来源3]    │
                    └─────────────────────────────┘
```

## Admin Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/trigger-rss` | POST | Manually trigger RSS fetch + summarize + store |
| `/admin/trigger-push?time=09:00&limit=N` | POST | Manually trigger card delivery for a time slot |
| `/admin/test-card?chat_id=xxx` | POST | Send a test card to verify button interactions |
| `/admin/backfill-chromadb?max_articles=N` | POST | Backfill existing MySQL articles into ChromaDB |

## Data Sources

SOURCES config in `app/main.py` — 10 sources across 6 vendors:

| Vendor | Blog | Twitter (Nitter) |
|--------|------|-------------------|
| OpenAI | openai.com/blog RSS | @OpenAI |
| Anthropic | anthropic.com/blog RSS | @AnthropicAI |
| Google DeepMind | blog.google RSS | @GoogleDeepMind |
| DeepSeek | — (SPA, unscrapable) | @deepseek_ai |
| Kimi (Moonshot) | kimi.com/blog scraper | @MoonshotAI |
| Z.ai / 智谱 | — (no blog) | @zhipuai |

Each source entry: `{vendor, channel ("Blog"|"Twitter"), url, fetcher ("rss"|"kimi"), filter (optional)}`

## Card Design (Plan A)

```
┌─────────────────────────────┐
│ Header (blue): 厂商名称      │
├─────────────────────────────┤
│ 📰 **Blog** · 2026-08-07    │  ← channel icon + type + date
│ ─────────────────────────── │
│ **文章标题**                  │
│ ─────────────────────────── │
│ 💡 **核心要点总结**           │
│   1. 要点一                  │
│   2. 要点二                  │
│   3. 要点三                  │
│ ─────────────────────────── │
│ [📖 阅读原文]                │  ← primary button → raw_url
│ [🔕 退订此厂商] [⚙️ 设置]    │  ← action buttons
└─────────────────────────────┘
```

Channel icons: 📰 Blog, 🐦 Twitter. Function signature: `build_news_card(title, vendor, summary_points, raw_url, published_at, channel="Blog")`

Additional card types in `app/feishu/card_builder.py`:
- **Subscription confirmations** — `build_subscription_reply(action, vendor)` for subscribe/unsubscribe feedback
- **Subscription list** — `build_subscription_list_card(subscribed)` showing all 6 vendors with status
- **Welcome cards** — `build_welcome_card()` (private chat) and `build_group_welcome_card()` (group onboarding)
- **Settings panel** — `build_settings_card(subscribed, push_time, frequency)` with interactive time/freq buttons
- **RAG Answer** — `build_rag_answer_card(answer_text, sources, original_query)` with 🤖 AI 行业情报 header (green), user question, LLM answer with citation markers, and 📚 source buttons linking to original articles

## Subscription System

Commands (Chinese + English, detected via regex in `app/subscription/handler.py`):

| Command | Example |
|---------|---------|
| Subscribe | `@Bot 订阅 OpenAI` / `subscribe Anthropic` |
| Unsubscribe | `@Bot 退订 OpenAI` / `unsubscribe Anthropic` |
| List | `@Bot 订阅列表` / `list subscriptions` |
| Settings | `@Bot 设置` / `settings` |
| Set Time | `@Bot 设置推送时间 晚上6点` / `set push time 18:00` |
| Set Frequency | `@Bot 设置频率 仅工作日` / `set frequency weekdays` |

**Push times:** 09:00 (早上9点), 12:00 (中午12点), 18:00 (下午6点). Default: 09:00.
**Frequencies:** daily (每天), weekdays (仅工作日), weekly_monday (每周一汇总). Default: daily.

**Permission model:** Group chats — only group owner can modify subscriptions. Private chats — user manages their own. Enforced in `app/chat/lifecycle.py:can_manage_subscription()`.

**Onboarding flow:** Bot added to group → auto-subscribe all vendors → send group welcome card. Private chat → first non-command message triggers welcome card with subscription guide.

## Development Process

This project follows **TDD** per the implementation plan in `implementation-plan.md`. The plan defines 7 sequential tasks, each requiring tests written first (`tests/`), then implementation, then `pytest -v` verification.

All 7 tasks are complete. RAG upgrade (+5 phases) and query accuracy bug fixes delivered. Current test suite: **247 tests passing** across 17 test files.

## Monitoring Stack

```bash
docker-compose up -d --build   # starts app + mysql + redis + prometheus + grafana
```

| Service | URL | Purpose |
|---------|-----|---------|
| App Metrics | `http://localhost:8000/metrics` | Prometheus text-format metrics endpoint |
| Prometheus | `http://localhost:9090` | Metrics collection + query (15s scrape interval) |
| Grafana | `http://localhost:3000` (admin/admin) | Pre-built dashboard: "Feishu AI News Bot — 运行监控" |

### Metric Categories

| Category | Metrics | Instrumentation |
|----------|---------|----------------|
| HTTP | request count + latency by method/path | FastAPI middleware |
| RSS Pipeline | articles fetched/processed/skipped, job duration, graph errors | Inline in `process_rss_job()` |
| Deliver Pipeline | cards sent, errors by push_time | Inline in `deliver_job()` |
| LLM Calls | latency, errors by operation (summarize/intent/router/rag_answer) | Decorator on `_call_llm_*()` |
| Feishu API | latency, errors by method/code | Decorator on `_send_card_impl()` |
| Circuit Breaker | state gauge (CLOSED/OPEN/HALF_OPEN) | `_emit_state_metric()` on transitions |
| WebSocket | connection status, disconnect count | Inline in `run_ws_client()` |
| Scraping | success/failure by fetcher_type | Inline in `scrape_article_text()` |
| RAG | embed duration/errors/total, retrieve duration, answer duration, query total by type | Inline in `embedder.py`, `rag_retrieve.py`, `rag_answer.py` |

See `docs/adr/0007-observability-stack.md` for design rationale and `monitoring/` for Prometheus + Grafana config files.

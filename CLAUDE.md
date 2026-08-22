# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Feishu AI News Bot — a FastAPI + LangGraph multi-platform service that polls AI vendor news sources (Blog RSS + Twitter via Nitter) daily, summarizes articles via LLM, and pushes rich messages to **Feishu (Lark)**, **Telegram**, and **Discord**. Supports @Bot queries from within chat groups, with RAG-powered semantic search and AI-assisted Q&A.

**Features:** 10 sources across 6 vendors (Blog RSS + Twitter via Nitter). Multi-platform delivery (Feishu Interactive Cards + Telegram Markdown/InlineKeyboard + Discord Embed/Button). Dynamic chat discovery via Feishu WebSocket + Telegram Webhook + Discord Gateway. Per-chat vendor subscriptions with push time & frequency customization. Group owner/admin permission control. Multi-LLM support (OpenAI / Anthropic / DeepSeek). RAG intelligent Q&A — semantic search (ChromaDB + OpenAI embeddings) with LLM-generated answers with citations. Intent routing: auto-classifies queries as "list search" vs "natural language Q&A".

**Scheduling:** Articles are fetched and summarized at 5:00 AM daily, then delivered at 9:00 / 12:00 / 18:00 based on each chat's time preference. Multi-layer push filtering: push_time → frequency (daily/weekdays/weekly) → vendor subscription. Delivery is platform-aware — each chat gets messages rendered in its native format.

**Event receiving:**
- **Feishu:** WebSocket long connection via `lark-oapi` SDK — no public URL or webhook needed. Events handled in a daemon thread with independent asyncio event loop.
- **Telegram:** Webhook via `python-telegram-bot` — FastAPI route at `/webhook/telegram` receives Update objects. `my_chat_member` events auto-register groups.
- **Discord:** Gateway via `discord.py` — daemon thread with independent asyncio loop (no public URL). `@Bot` mention messages / button interactions / guild lifecycle events drive onboarding. Business logic offloaded to a `ThreadPoolExecutor(max_workers=5)` so the gateway loop never blocks on LLM calls.

FastAPI serves `/health`, `/metrics`, `/admin/*`, and the Telegram webhook endpoint.

**Observability:** Structured JSON logging (`python-json-logger`), Prometheus metrics (30 metric families across HTTP, LLM, Feishu API, Telegram API, Pipeline, CircuitBreaker, WebSocket, RAG, Query pool), Grafana dashboard (8-row pre-built dashboard). Full monitoring stack via `docker-compose` (Prometheus + Grafana).

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, LangChain Core, Pydantic v2, SQLAlchemy, Redis-py, APScheduler, lark-oapi, python-telegram-bot, Trafilatura, feedparser, httpx, ChromaDB, OpenAI embeddings, Pytest, Docker, Prometheus, Grafana.

**Code style:** Comments in Chinese, variable/function/class names in English.

## Design Patterns

| Pattern | Location | Type |
|---------|----------|------|
| **Repository (ABC)** | `app/db/repositories.py` → `sql_repositories.py` | Data access abstraction; `handler.py`/`lifecycle.py` are facades |
| **Circuit Breaker** | `app/core/resilience.py` | 3-state machine (CLOSED/OPEN/HALF_OPEN) for Feishu API calls |
| **Producer-Consumer** | `app/feishu/event_router.py` | `ThreadPoolExecutor(max_workers=5)` offloads event processing from WS thread |
| **Factory** | `app/llm/provider.py` | `get_llm()` returns provider-specific `BaseChatModel` |
| **Factory** | `app/platforms/registry.py` | `get_platform_adapter()` returns platform-specific `PlatformAdapter` |
| **Adapter (Platform)** | `app/platforms/` | `PlatformAdapter` ABC → `FeishuAdapter` / `TelegramAdapter`; isolates IM platform specifics |
| **Strategy** | `app/fetcher/` | RSS vs Kimi HTML scraper selected by `source["fetcher"]` |
| **Builder** | `app/feishu/card_builder.py` | 7 card type builders (news, RAG answer, subscription, welcome, settings) |
| **Observer** | `app/feishu/event_router.py` | SDK `EventDispatcherHandler` routes events to typed handlers |
| **Facade** | `app/subscription/handler.py`, `app/chat/lifecycle.py` | Module-level functions delegate to Repository, preserving backward compat |
| **Decorator** | `app/core/metrics.py` | `@track_llm_call`, `@track_feishu_api`, `@track_job_metrics` — non-invasive metric instrumentation |
| **Registry (isolated)** | `app/core/metrics.py` | `CollectorRegistry()` singleton — metrics isolated from global Prometheus registry |
| **Strategy (intent routing)** | `app/graph/nodes/intent_router.py` | LLM classification + keyword heuristic fallback: list vs qa routing |

**Concurrency Model:** Unified bounded query pool — `app/core/query_executor.py` (shared `ThreadPoolExecutor` + `BoundedSemaphore` + per-user token-bucket rate limit). All three platforms dispatch into it and **never block their event loops**: Feishu WS thread / Discord gateway thread / Telegram webhook (FastAPI route) each just `submit()` and return. Overload policy: queue full → drop + platform layer replies a "系统繁忙" message; per-user **token bucket** (`QUERY_RATE_BURST=3`, `QUERY_RATE_REFILL=0.5/s`) allows short bursts but throttles sustained spam (`QUERY_RATE_LIMIT_SECONDS=0` disables). Config: `QUERY_MAX_WORKERS=10`, `QUERY_MAX_QUEUE=50`, `QUERY_QUEUE_TIMEOUT_SECONDS=0.5`. Observability: `query_queue_depth` / `query_workers_busy` / `query_dropped_total` / `query_processed_total` / `query_queue_wait_seconds`. Shared state via MySQL/Redis/TTL Cache (threading.Lock); ChromaDB lazy-init is double-check-locked (`vector_store.py`). See `docs/concurrency-model.md` for full analysis.

**Async execution mode (feature-flagged):** `QUERY_EXECUTOR_MODE=thread|async` (default `thread`) switches the query pool between the sync `ThreadPoolExecutor` and an asyncio coroutine pool (`app/core/async_query_executor.py` — independent event-loop daemon thread + `asyncio.Semaphore(QUERY_MAX_CONCURRENCY=100)` backpressure + per-task `wait_for(QUERY_TASK_TIMEOUT_SECONDS=120)`). Under `async`, main.py lifespan registers the async executor via `query_executor.set_submit_delegate()`, so all three platforms' `submit()` calls transparently route to the coroutine pool — no platform-layer change. Token-bucket rate limiting is shared across both paths. `_run_task` handles both sync closures (`asyncio.to_thread`) and native coroutine functions (`await` directly), so nodes can migrate to `ainvoke` incrementally. Benchmark: `scripts/benchmark_query.py` → `docs/benchmark-results.md` (thread QPS 20 → async QPS 195, 9.7x on IO-bound load). See ADR-0013.

**Service governance:** LLM calls go through `llm_circuit_breaker` (`app/llm/provider.py` — reuses `resilience.py:CircuitBreaker`, 5-failure threshold → OPEN → fast-fail <1ms instead of retrying 30s and draining a worker). On `CircuitBreakerOpenError` the three LLM nodes degrade gracefully: `intent_router`/`intent` fall back to keyword heuristics, `rag_answer` returns the retrieved article list ("AI 生成服务暂时繁忙…"). Degradation counted via `degraded_requests_total{path}`. See ADR-0012.

**Multi-level cache:** `app/core/multi_cache.py` — L1 in-process LRU+TTL (extends `cache.py:TTLCache`) + L2 Redis, with singleflight (cache-breakdown guard), null-value sentinel (penetration guard), and ±10% TTL jitter (avalanche guard). Three application points: LLM results (`llm:{op}:{sha256}`, TTL 1h; summarize not cached — article content never repeats), embeddings (`embed:v1:{sha256}`, TTL 24h — deterministic), DB hot reads (subscriptions/preferences, TTL 5min, **invalidated on write**). Metrics: `cache_hit_total{cache,level}` / `cache_miss_total{cache}`. Config: `CACHE_L1_MAXSIZE=2000`, `CACHE_LLM_TTL`, `CACHE_EMBED_TTL`, `CACHE_DB_TTL`. See ADR-0010.

**Delivery queue (Redis Stream):** the push pipeline (`deliver_job`) is a producer that enqueues `{article_id, platform, conversation_id, push_time, retry_count, enqueued_at}` to `app/queue/stream_queue.py` (Stream `feishu_bot:deliver_stream`, group `deliver_workers`); `app/queue/deliver_consumer.py` runs `DELIVER_CONSUMERS=4` consumer threads + 1 maintenance thread. At-least-once delivery: XREADGROUP → render → send → XACK; crashed-consumer messages reclaimed via XAUTOCLAIM (`claim_stale`, 60s idle), `retry_count++`, DLQ after `DELIVER_MAX_RETRY=3`. Idempotency lock (`SET sent:{article_id}:{platform}:{conversation_id} NX EX 86400`) prevents duplicate sends on redelivery. Redis down → `deliver_job` falls back to inline synchronous send (`queue_fallback_total`). **Query pipeline deliberately does NOT use the queue** (interactive users wait live; at-least-once would replay stale replies after restart). Metrics: `deliver_queue_depth` / `deliver_pending_messages` / `deliver_enqueued_total` / `deliver_consumed_total{platform}` / `deliver_retry_total` / `deliver_dlq_total`. See ADR-0011.

**Architecture Decisions:** 13 ADRs in `docs/adr/` — WebSocket choice, two-phase pipeline, WS thread isolation, LangGraph workflow, soft-delete, thread-pool-over-asyncio, observability stack, RAG upgrade, multi-platform adapter, multi-level cache (0010), Redis Stream delivery queue (0011), LLM circuit breaker + degradation (0012), and async query pipeline (0013, partially supersedes 0006).

**Database Migrations:** Lightweight auto-migration in `app/db/database.py:_run_migrations()` — detects missing columns on startup and applies ALTER TABLE, then `_run_index_migrations()` does the same for indexes (via `inspector.get_indexes()`). Safe, idempotent, no external migration framework needed. Fresh databases get everything from `Base.metadata.create_all()`; the migration lists exist only for pre-existing databases, so any index added to `models.py` must also be added to `_INDEX_MIGRATIONS` (a test asserts the two stay in sync).

## Commands

```bash
# Install (development — floor constraints)
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Install (production — exact pins)
pip install -r requirements-lock.txt

# Run (requires MySQL + Redis)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Docker
docker-compose up -d --build

# Tests
pytest -v
```

**Dependency files:** `requirements.txt` holds `>=` floor constraints; `requirements-lock.txt` holds exact pins and **is committed** (it was previously gitignored while `requirements.txt` told production to install from it — a contradiction that left the lock file out of the repo entirely). Regenerate it **from the project venv, never from a conda base env** — a conda `pip freeze` emits `@ file:///C:/b/...` local build paths that cannot install anywhere else:

```bash
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m pip freeze --exclude-editable > requirements-lock.txt
```

Add any new dependency to **both** files. `chromadb` and `discord.py` are required for the full test suite — without them 12 tests fail on import.

## Architecture

### Multi-Platform Adapter Pattern

```
                    ┌──────────────────────────────┐
                    │     Core Business Logic       │
                    │  (subscription, graph, chat)  │
                    └──────────────┬───────────────┘
                                   │  uses
                    ┌──────────────▼───────────────┐
                    │     PlatformAdapter (ABC)     │
                    │   app/platforms/adapter.py    │
                    ├──────────────────────────────┤
                    │ + send_message(target, msg)   │
                    │ + get_conversation_info(id)   │
                    │ + get_platform_name()         │
                    └──────────────┬───────────────┘
                                   │  implements
                    ┌──────────────┼───────────────┐
                    │              │               │
              ┌─────▼─────┐ ┌──────▼──────┐ ┌──────▼──────┐
              │  Feishu   │ │  Telegram   │ │  Discord    │  (Slack...)
              │  Adapter  │ │  Adapter    │ │  Adapter    │
              └───────────┘ └─────────────┘ └─────────────┘
```

Core business logic operates on `RichMessage` (platform-agnostic). Each adapter renders it:
- **Feishu:** `RichMessage` → Interactive Card JSON (lark_md elements + action buttons)
- **Telegram:** `RichMessage` → HTML text + InlineKeyboardMarkup
- **Discord:** `RichMessage` → Embed JSON + Button components

The pattern mirrors the existing LLM Provider Factory (`app/llm/provider.py`).

### Platform Module Structure

```
app/platforms/
├── adapter.py                # PlatformAdapter ABC (5 abstract methods)
├── message_model.py          # RichMessage, ActionButton, CallbackData, ConversationInfo, IncomingMessage
├── registry.py               # Platform registration + discovery (get_platform_adapter, list_available_platforms)
├── feishu/
│   ├── adapter.py            # FeishuAdapter — wraps existing FeishuClient
│   └── renderer.py           # RichMessage → Feishu Interactive Card JSON
├── telegram/
│   ├── adapter.py            # TelegramAdapter — python-telegram-bot Bot instance
│   ├── renderer.py           # RichMessage → HTML + InlineKeyboardMarkup
│   ├── webhook.py            # FastAPI webhook endpoint (/webhook/telegram)
│   └── commands.py           # Command templates + vendor alias resolution
└── discord/
    ├── adapter.py            # DiscordAdapter — discord.py client (gateway thread)
    ├── renderer.py           # RichMessage → Discord Embed JSON + Button components
    ├── gateway.py            # daemon thread + discord.py Client + worker pool + dedup
    └── commands.py           # Discord-flavored welcome/help templates + vendor alias
```

### Data Model (Multi-Platform)

Three tables store platform metadata alongside existing `chat_id`:

```
Subscription:   platform | conversation_id | chat_id (compat) | vendor | is_active
ChatPreference: platform | conversation_id | chat_id (compat) | push_time | frequency
ChatRegistry:   platform | conversation_id | chat_id (compat) | chat_type | owner_id | is_active
```

- `platform` = `"feishu"` | `"telegram"` | `"discord"` — which IM platform
- `conversation_id` = platform-native chat/user ID (e.g., `oc_xxx` for Feishu, `-123456` for Telegram group, `1234567890` Discord channel snowflake)
- `chat_id` = legacy compat column, set equal to `conversation_id` for existing data
- All repository/facade functions accept optional `platform="feishu"` parameter (backward compatible)

### Conversation Discovery (no static ID lists)

**Conversation IDs are never pre-configured — every platform discovers them at runtime** and writes them into `chat_registry`. There is no "fill in your channel IDs" step for any platform.

| Platform | Discovery trigger | Code path |
|----------|-------------------|-----------|
| Feishu | Bot added to group → `im.chat.member.bot.added_v1`; private chat → first message | `feishu/event_router.py` → `chat/lifecycle.register_chat()` |
| Telegram | `my_chat_member` (bot added/removed); private chat → first `/start` or text | `platforms/telegram/webhook.py` → `main._auto_detect_and_register_telegram_chat()` |
| Discord | `on_guild_join` / `on_ready` → `_pick_default_channel()`; **or** any channel where a user `@Bot`s | `platforms/discord/gateway.py:_onboard_guild()` → `main._auto_register_discord_channel()` |

Discord specifics: `_pick_default_channel()` prefers `system_channel`, else the first text channel where the bot has `send_messages`. `on_ready` re-onboards existing guilds so a restart (or being invited while offline) still registers — `is_new_chat()` keeps it idempotent. Server channels register as `chat_type="group"` and auto-subscribe all vendors; DMs register as `"user"` without auto-subscription. `DISCORD_GUILD_ID` is **optional** — it only restricts onboarding to a single server, it is not an ID registry.

Delivery is symmetric: `deliver_job()` queries `ChatRegistry` and groups by `platform`, so it picks up whatever was discovered without config changes.

> `FEISHU_CHAT_IDS` in `.env.example` is the one remaining static list, and it is vestigial — only `graph/nodes/send_feishu.py:_resolve_targets_legacy()` still reads it. `deliver_job()` ignores it entirely.

**Platform isolation invariant:** `get_active_chats()` / `get_active_chat_ids()` (`db/sql_repositories.py`) take `platform` (default `"feishu"`; pass `None` for a deliberate cross-platform scan, which also returns a `platform` field for routing). A conversation ID is only meaningful within its own platform — handing Discord channel snowflakes to `FeishuClient` yields nothing but failed sends, so any new call site must pass the platform it intends to send through.

### Scheduling (Two-Phase Pipeline)

```
05:00 — process_rss_job()  → fetch + summarize + store (no push)
09:00 — deliver_job("09:00") → query today's articles → filter → send (multi-platform)
12:00 — deliver_job("12:00") → query today's articles → filter → send (multi-platform)
18:00 — deliver_job("18:00") → query today's articles → filter → send (multi-platform)
```

The 5 AM batch uses `build_push_graph(push_enabled=False)` — it only runs extract→summarize→store, skipping card building and sending. Delivery jobs query stored articles and apply three-layer filtering per-platform, per-chat: push_time preference, frequency setting, and vendor subscription.

### WebSocket Event Flow (Feishu)

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
        └── news query?          → BotQueryGraph (platform="feishu")
```

- `app/feishu/ws_client.py` — daemon thread, independent `asyncio` loop (patches SDK module-level loop), auto-reconnect (exponential backoff 1s→60s)
- `app/feishu/event_router.py` — typed SDK handlers → existing business logic, message dedup via OrderedDict cache, `QueryState.platform = "feishu"`

### Webhook Event Flow (Telegram)

```
Telegram Server → POST /webhook/telegram (FastAPI route)
                        │
                        ▼
              handle_telegram_webhook()
                        │
        ┌───────────────┼──────────────────┐
        ▼               ▼                  ▼
  _handle_message  _handle_callback  _handle_my_chat_member
  (text + cmd)     (button click)    (bot added/removed)
        │
        ├── /command detected? → _handle_telegram_command()
        │   (permission check via TelegramAdapter.is_admin())
        │   (auto-register chat on first message)
        │
        └── NL query? → BotQueryGraph (platform="telegram")
```

- `app/platforms/telegram/webhook.py` — FastAPI route + secret token validation + event dispatch
- `app/platforms/telegram/commands.py` — vendor alias resolution, welcome/help message templates
- `app/main.py` — `_handle_telegram_command()`, `_handle_telegram_callback_action()`, `_auto_detect_and_register_telegram_chat()`

### LangGraph Workflows

Two LangGraph workflows drive the system:

1. **NewsPushGraph** (`app/graph/news_push_graph.py`) — periodic pipeline:
   `[Start] → ExtractNode → SummarizeNode → StoreNode → BuildCardNode → SendFeishuNode → [End]`
   - `extract.py` — fetches raw article content via Trafilatura
   - `summarize.py` — LLM distills 3 key points (with 3x retry)
   - `store.py` — persists to MySQL using state's `published_at` field
   - `build_card.py` — builds Feishu Interactive Card JSON from state fields
   - `send_feishu.py` — resolves dynamic targets via chat_registry + subscription filter, sends cards
   - Supports checkpointing (`enable_checkpoint`) and human-in-the-loop (`enable_human_review`)
   - Conditional edges: FAILED status routes directly to END

2. **BotQueryGraph** (`app/graph/bot_query_graph.py`) — interactive pipeline with conditional routing:
   ```
   [Start] → IntentRouterNode
                ├─ "list" → IntentNode → SearchDBNode → FormatResponseNode → ReplyFeishuNode → [End]
                └─ "qa"   → RAGRetrieveNode → RAGAnswerNode → FormatRAGResponseNode → ReplyFeishuNode → [End]
   ```
   - `intent_router.py` — LLM 3-class classification (list/qa/command) with 2x retry + keyword heuristic fallback
   - `intent.py` — parses user NL into structured query (vendor via alias map, date range `_extract_days_from_query()`), LLM with 3x retry + keyword fallback
   - `search_db.py` — queries MySQL for matching articles by vendor + calendar day range (`_calc_since()` uses 00:00 UTC boundaries)
   - `rag_retrieve.py` — semantic search: query embedding → ChromaDB top-K → MySQL backfill `raw_content`
   - `rag_answer.py` — LLM reads context + user question → comprehensive answer with `[来源 N]` citations
   - `format_response.py` — builds **platform-agnostic RichMessage** (Markdown body + ActionButtons)
   - `format_rag_response.py` (inline in bot_query_graph.py) — wraps RAG answer into RichMessage
   - `reply_feishu.py` — **platform-aware:** checks `state["platform"]`, renders RichMessage via adapter, falls back to legacy FeishuClient for backward compat

### Key Modules

**Application Layer:**
- `app/main.py` — FastAPI entry point, SOURCES config, APScheduler lifecycle (4 jobs), `/health`, `/metrics`, `/admin/*` endpoints, Telegram webhook setup + command/callback handlers
- `app/core/config.py` — all config via `pydantic-settings` (env vars): Feishu, Telegram, Discord, LLM (incl. `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES`), MySQL, Redis, `LOG_LEVEL`, `ADMIN_API_TOKEN`, `QUERY_*` concurrency pool settings
- `app/core/query_executor.py` — unified bounded query pool: `submit(fn, user_id=...) -> QuerySubmitStatus` (ACCEPTED/QUEUE_FULL/RATE_LIMITED), `BoundedSemaphore` backpressure, per-user rate limiting, Prometheus metrics; all platforms dispatch here
- `app/core/logging_config.py` — structured JSON logging via `python-json-logger`, configurable `LOG_LEVEL`, suppresses noisy third-party libs
- `app/core/metrics.py` — 30 Prometheus metric families (counters/gauges/histograms), decorator/context-manager instrumentation, isolated `CollectorRegistry`
- `app/core/security.py` — [已废弃] Feishu webhook 签名验证，WebSocket 模式无需验签

**Platform Adapter Layer:**
- `app/platforms/adapter.py` — `PlatformAdapter` ABC: `send_message()`, `get_conversation_info()`, `get_platform_name()`, `get_platform_label()`, lifecycle hooks
- `app/platforms/message_model.py` — `RichMessage` (title, body, buttons, color_hint, footer), `ActionButton` (label, action, value, style), `CallbackData`, `ConversationInfo`, `IncomingMessage`
- `app/platforms/registry.py` — `get_platform_adapter(platform, settings)` factory, `list_available_platforms()`, `is_platform_configured()`

**Feishu Integration:**
- `app/feishu/client.py` — Feishu Open API via `lark-oapi` SDK, auto-managed token
- `app/feishu/card_builder.py` — card builders: `build_news_card()` (Plan A), `build_rag_answer_card()` (RAG Q&A), `build_subscription_reply()`, `build_subscription_list_card()`, `build_welcome_card()`, `build_group_welcome_card()`, `build_settings_card()`
- `app/feishu/event_router.py` — WebSocket event dispatcher: typed SDK handlers for messages, card actions, bot lifecycle events. Events dispatched via `ThreadPoolExecutor`. Passes `platform="feishu"` in QueryState.
- `app/feishu/ws_client.py` — WS thread manager: independent event loop, auto-reconnect with exponential backoff, daemon thread for FastAPI coexistence
- `app/platforms/feishu/adapter.py` — `FeishuAdapter` wrapping `FeishuClient` for `PlatformAdapter` interface
- `app/platforms/feishu/renderer.py` — `RichMessage` → Feishu Interactive Card JSON

**Telegram Integration:**
- `app/platforms/telegram/adapter.py` — `TelegramAdapter` using `python-telegram-bot` Bot instance; includes `is_admin()` for group permission checks
- `app/platforms/telegram/renderer.py` — `RichMessage` → HTML text + InlineKeyboardMarkup; long message auto-chunking at 4000 chars
- `app/platforms/telegram/webhook.py` — FastAPI webhook endpoint + `my_chat_member` event for group lifecycle
- `app/platforms/telegram/commands.py` — vendor alias resolution, welcome/help message templates

**Discord Integration:**
- `app/platforms/discord/adapter.py` — `DiscordAdapter` using `discord.py`; `send_message` dispatches onto the gateway loop via `asyncio.run_coroutine_threadsafe`; `is_admin()` checks `guild_permissions.administrator`/`manage_guild` via `fetch_member` (no Members Intent needed)
- `app/platforms/discord/renderer.py` — `RichMessage` → Discord Embed dict (`color_hint`→embed color) + Button component specs (URL buttons / callback `custom_id`); pure functions, no SDK coupling
- `app/platforms/discord/gateway.py` — daemon thread + `discord.Client` (independent asyncio loop); `@Bot` mention stripping, `on_interaction` button ack, guild join/remove onboarding, `OrderedDict` dedup; processing offloaded to `ThreadPoolExecutor(max_workers=5)` (Producer-Consumer, mirrors `feishu/event_router.py`)
- `app/platforms/discord/commands.py` — Discord-flavored welcome/help templates + `resolve_vendor()` (reuses `subscription/handler.py` alias table)

**RAG (Retrieval-Augmented Generation):**
- `app/rag/embedder.py` — OpenAI `text-embedding-3-small` (1536-dim) via `openai.OpenAI` client, 3x retry, Prometheus metrics
- `app/rag/vector_store.py` — ChromaDB `PersistentClient` (local `./chroma_data/`), CRUD operations: `add_article()`, `search()`, `delete_article()`, `collection_count()`
- `app/prompts/rag_answer.yaml` — RAG answer prompt with citation rules

**Data Fetching:**
- `app/fetcher/rss_fetcher.py` — RSS/Atom parsing via feedparser, `detect_vendor()` utility
- `app/fetcher/kimi_scraper.py` — Kimi Blog HTML scraper (no RSS feed available)
- `app/fetcher/web_scraper.py` — article full-text extraction via Trafilatura (3x exponential backoff)

**Storage:**
- `app/db/models.py` — SQLAlchemy ORM: `NewsArticle` (with `raw_content` column for RAG), `Subscription` (with `platform` + `conversation_id`), `ChatPreference` (with `platform` + `conversation_id`), `ChatRegistry` (with `platform` + `conversation_id`). Indexes: `(platform, conversation_id)` on all three multi-platform tables (the primary lookup key — its leftmost prefix also covers plain `platform` lookups, so `platform` is not separately indexed), `(platform, is_active)` on `chat_registry` for the delivery scan, and `published_at` + `(vendor, published_at)` on `news_articles` for the delivery/search date-range filters
- `app/db/database.py` — SQLAlchemy session factory + `_run_migrations()` auto-migration (12 migrations including multi-platform columns)
- `app/db/redis.py` — URL dedup cache (Redis Set) + `tenant_access_token` cache
- `app/db/repositories.py` — Repository ABC interfaces (`SubscriptionRepository`, `ChatRegistryRepository`) with `platform` parameters
- `app/db/sql_repositories.py` — SQLAlchemy Repository implementations; all CRUD methods platform-aware; `replace_repos()` for test injection
- `app/core/cache.py` — Thread-safe TTL memory cache (300s). Caches chat_type, owner_id, preferences (keys now scoped by `platform:chat_id`)

**LLM:**
- `app/llm/provider.py` — Factory for multi-provider LLM (OpenAI/Anthropic/DeepSeek), returns `BaseChatModel`. Always sets `timeout` + `max_retries` — LLM calls run on `query_executor` workers, and an unbounded hang would permanently consume one, silently draining the bounded pool while `/health` still reports OK
- `app/prompts/loader.py` — YAML-based prompt template loader (`intent.yaml`, `summarize.yaml`, `rag_answer.yaml`)

**Subscription & Chat Management:**
- `app/subscription/handler.py` — subscribe/unsubscribe/list commands, vendor aliases (12+ mappings), command regex detection, push time/frequency preferences. All facade functions accept `platform` parameter (default `"feishu"`). Constant: `ALL_VENDORS`, `PUSH_TIMES` (09:00/12:00/18:00), `FREQUENCIES` (daily/weekdays/weekly_monday)
- `app/chat/lifecycle.py` — chat auto-registration on bot added, deactivation on bot removed, active chat queries, owner_id caching, permission check (`can_manage_subscription()` now multi-platform: Feishu owner_id vs Telegram `is_admin()`)

**State definitions** are in `app/graph/state.py` — `PushState` (raw_url, raw_content, vendor, title, published_at, summary_points, card_json, status) and `QueryState` (platform, user_id, chat_id, user_query, parsed_intent, query_results, query_type, rag_context, rag_answer, reply_card_json, rich_message) TypedDicts.

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
                          format_rag_response → reply_feishu (platform-aware)
                            │
                            ├─ Feishu: RichMessage → Interactive Card JSON
                            ├─ Telegram: RichMessage → HTML + InlineKeyboard
                            ▼
                    ┌─────────────────────────────┐
                    │ 🤖 AI 行业情报 (green)       │
                    │ 💬 GPT-5 什么时候发布？      │
                    │ ...LLM answer...             │
                    │ 📚 [来源1] [来源2] [来源3]    │
                    └─────────────────────────────┘
```

### Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + feature guide |
| `/subscribe OpenAI` | Subscribe to a vendor (or `/subscribe all`) |
| `/unsubscribe DeepSeek` | Unsubscribe from a vendor |
| `/list` | View current subscriptions |
| `/settings` | View push time & frequency settings |
| `/settime 18:00` | Set push time (09:00 / 12:00 / 18:00) |
| `/setfrequency weekdays` | Set frequency (daily / weekdays / weekly_monday) |
| `/help` | Show help message |

NL queries also supported: "OpenAI 最近有什么新闻", "GPT-5 什么时候发布", etc.

### Telegram Configuration

```bash
# .env
TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
# Optional: webhook secret for security
TELEGRAM_WEBHOOK_SECRET=random_secret_string
# Optional: custom webhook path (default: /webhook/telegram)
TELEGRAM_WEBHOOK_PATH=/webhook/telegram
```

When `TELEGRAM_BOT_TOKEN` is empty, Telegram integration silently disables — Feishu continues working normally.

### Discord Configuration

```bash
# .env
DISCORD_BOT_TOKEN=your_bot_token_from_Developer_Portal
# Optional: restrict onboarding to a single server
DISCORD_GUILD_ID=
```

**Setup steps:**
1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications) and copy its token.
2. Enable the **Message Content Intent** (Bot → Privileged Gateway Intents) — required to read `@Bot` channel text.
3. Invite the bot to your server with `applications.commands` + `bot` + `send_messages` scopes.

When `DISCORD_BOT_TOKEN` is empty, Discord integration silently disables — Feishu continues working normally.

## Admin Endpoints

**All `/admin/*` endpoints require authentication.** Requests must carry `X-Admin-Token: <ADMIN_API_TOKEN>`. The check is fail-closed: when `ADMIN_API_TOKEN` is unset the endpoints return `503` rather than running unauthenticated — they can trigger mass message delivery and batch embedding billing. Enforced by `verify_admin_token()` in `app/main.py` (constant-time compare via `secrets.compare_digest`).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/trigger-rss` | POST | Manually trigger RSS fetch + summarize + store |
| `/admin/trigger-push?time=09:00&limit=N` | POST | Manually trigger card delivery for a time slot |
| `/admin/test-card?chat_id=xxx` | POST | Send a test card to verify button interactions |
| `/admin/backfill-chromadb?max_articles=N` | POST | Backfill existing MySQL articles into ChromaDB |

```bash
curl -X POST http://localhost:8000/admin/trigger-rss -H "X-Admin-Token: $ADMIN_API_TOKEN"
```

`/health`, `/health/detailed`, `/metrics` and the Telegram webhook remain public.

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

## Card Design (Feishu — Plan A)

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

## Message Design (Telegram)

```
🤖 OpenAI
📰 **GPT-5 Released**
💡 **核心要点总结**
  1. Point one
  2. Point two
  3. Point three
[📖 阅读原文] [🔕 退订 OpenAI]
─────────────────────
📅 2026-08-07
```

Telegram messages use HTML formatting + InlineKeyboardMarkup. Buttons: URL buttons for links, callback buttons for subscribe/unsubscribe/settings actions. Long messages auto-chunk at 4000 characters.

## Subscription System

Commands (Chinese + English, detected via regex in `app/subscription/handler.py`):

| Command | Example |
|---------|---------|
| Subscribe | `@Bot 订阅 OpenAI` / `subscribe Anthropic` / `/subscribe OpenAI` |
| Unsubscribe | `@Bot 退订 OpenAI` / `unsubscribe Anthropic` / `/unsubscribe OpenAI` |
| List | `@Bot 订阅列表` / `list subscriptions` / `/list` |
| Settings | `@Bot 设置` / `settings` / `/settings` |
| Set Time | `@Bot 设置推送时间 晚上6点` / `set push time 18:00` / `/settime 18:00` |
| Set Frequency | `@Bot 设置频率 仅工作日` / `set frequency weekdays` / `/setfrequency workdays` |

**Push times:** 09:00 (早上9点), 12:00 (中午12点), 18:00 (下午6点). Default: 09:00.
**Frequencies:** daily (每天), weekdays (仅工作日), weekly_monday (每周一汇总). Default: daily.

**Permission model:** Group chats — only group owner (Feishu) or admin (Telegram) can modify subscriptions. Private chats — user manages their own. Enforced in `app/chat/lifecycle.py:can_manage_subscription()` which uses `FeishuClient.get_chat_info()` for Feishu and `TelegramAdapter.is_admin()` for Telegram.

**Onboarding flow:**
- **Feishu:** Bot added to group → auto-subscribe all vendors → send group welcome card. Private chat → first non-command message triggers welcome card with subscription guide.
- **Telegram:** `my_chat_member` event (bot added to group) → auto-register + auto-subscribe all + welcome message. Private chat → first `/start` or text message → auto-register + welcome message. Chat type auto-detected from chat_id sign (negative = group).

## Development Process

This project follows **TDD** per the implementation plan in `implementation-plan.md`. The plan defines 7 sequential tasks, each requiring tests written first (`tests/`), then implementation, then `pytest -v` verification.

All 7 tasks complete. RAG upgrade (+5 phases), query accuracy bug fixes, multi-platform adapter (+6 phases), Discord platform (+1 phase), the unified bounded query pool, and the multi-platform observability upgrade delivered. Current test suite: **333 tests passing** across 20 test files.

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

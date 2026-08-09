# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Feishu AI News Bot — a FastAPI + LangGraph service that polls AI vendor news sources (Blog RSS + Twitter via Nitter) daily, summarizes articles via LLM, and pushes Interactive Cards to Feishu (Lark). Supports @Bot queries from within Feishu groups.

**Features:** 10 sources across 6 vendors (Blog RSS + Twitter via Nitter). Dynamic chat discovery via Feishu WebSocket long connection (no hardcoded chat IDs). Per-chat vendor subscriptions with push time & frequency customization. Group owner permission control. Multi-LLM support (OpenAI / Anthropic / DeepSeek).

**Scheduling:** Articles are fetched and summarized at 5:00 AM daily, then delivered at 9:00 / 12:00 / 18:00 based on each chat's time preference. Multi-layer push filtering: push_time → frequency (daily/weekdays/weekly) → vendor subscription.

**Event receiving:** WebSocket long connection via `lark-oapi` SDK — no public URL or webhook needed. Events handled in a daemon thread with independent asyncio event loop. FastAPI serves `/health` and `/admin/trigger-rss` only.

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, LangChain Core, Pydantic v2, SQLAlchemy, Redis-py, APScheduler, lark-oapi, Trafilatura, feedparser, httpx, Pytest, Docker.

**Code style:** Comments in Chinese, variable/function/class names in English.

**Design Patterns:**

| Pattern | Location | Type |
|---------|----------|------|
| **Repository (ABC)** | `app/db/repositories.py` → `sql_repositories.py` | Data access abstraction; `handler.py`/`lifecycle.py` are facades |
| **Circuit Breaker** | `app/core/resilience.py` | 3-state machine (CLOSED/OPEN/HALF_OPEN) for Feishu API calls |
| **Producer-Consumer** | `app/feishu/event_router.py` | `ThreadPoolExecutor(max_workers=5)` offloads event processing from WS thread |
| **Factory** | `app/llm/provider.py` | `get_llm()` returns provider-specific `BaseChatModel` |
| **Strategy** | `app/fetcher/` | RSS vs Kimi HTML scraper selected by `source["fetcher"]` |
| **Builder** | `app/feishu/card_builder.py` | 6 card type builders |
| **Observer** | `app/feishu/event_router.py` | SDK `EventDispatcherHandler` routes events to typed handlers |
| **Facade** | `app/subscription/handler.py`, `app/chat/lifecycle.py` | Module-level functions delegate to Repository, preserving backward compat |

**Concurrency Model:** 3-thread architecture — FastAPI main (uvicorn asyncio), WS daemon (isolated event loop), Event worker pool (ThreadPoolExecutor). Shared state via MySQL/Redis/TTL Cache (threading.Lock). See `docs/concurrency-model.md` for full analysis.

**Architecture Decisions:** 6 ADRs in `docs/adr/` covering WebSocket choice, two-phase pipeline, WS thread isolation, LangGraph workflow, soft-delete, and thread-pool-over-asyncio.

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

2. **BotQueryGraph** (`app/graph/bot_query_graph.py`) — interactive pipeline:
   `[Start Webhook] → ParseIntentNode → SearchDBNode → FormatResponseNode → ReplyFeishuNode → [End]`
   - `intent.py` — parses user NL into structured query (vendor via alias map, date range), LLM with 3x retry + keyword fallback
   - `search_db.py` — queries MySQL for matching articles by vendor + date range
   - `format_response.py` — builds multi-article result card (or "未找到" empty card)
   - `reply_feishu.py` — sends reply card to chat_id from QueryState

### Key Modules

**Application Layer:**
- `app/main.py` — FastAPI entry point, SOURCES config, APScheduler lifecycle (4 jobs), `/feishu/events` webhook, `/admin/trigger-rss` manual trigger
- `app/core/config.py` — all config via `pydantic-settings` (env vars)
- `app/core/security.py` — [已废弃] Feishu webhook 签名验证，WebSocket 模式无需验签

**Feishu Integration:**
- `app/feishu/client.py` — Feishu Open API via `lark-oapi` SDK, auto-managed token
- `app/feishu/card_builder.py` — card builders: `build_news_card()` (Plan A), `build_subscription_reply()`, `build_subscription_list_card()`, `build_welcome_card()`, `build_group_welcome_card()`, `build_settings_card()`
- `app/feishu/event_router.py` — WebSocket event dispatcher: typed SDK handlers for messages, card actions, bot lifecycle events. Events dispatched via `ThreadPoolExecutor`
- `app/feishu/ws_client.py` — WS thread manager: independent event loop, auto-reconnect with exponential backoff, daemon thread for FastAPI coexistence

**Data Fetching:**
- `app/fetcher/rss_fetcher.py` — RSS/Atom parsing via feedparser, `detect_vendor()` utility
- `app/fetcher/kimi_scraper.py` — Kimi Blog HTML scraper (no RSS feed available)
- `app/fetcher/web_scraper.py` — article full-text extraction via Trafilatura (3x exponential backoff)

**Storage:**
- `app/db/models.py` — SQLAlchemy ORM: `NewsArticle`, `Subscription`, `ChatPreference`, `ChatRegistry`
- `app/db/database.py` — SQLAlchemy session factory
- `app/db/redis.py` — URL dedup cache (Redis Set) + `tenant_access_token` cache
- `app/db/repositories.py` — Repository ABC interfaces (`SubscriptionRepository`, `ChatRegistryRepository`)
- `app/db/sql_repositories.py` — SQLAlchemy Repository implementations; `replace_repos()` for test injection
- `app/core/cache.py` — Thread-safe TTL memory cache (300s). Caches chat_type, owner_id, preferences
- `app/core/resilience.py` — Circuit Breaker (3-state: CLOSED/OPEN/HALF_OPEN). Injected into `FeishuClient`

**LLM:**
- `app/llm/provider.py` — Factory for multi-provider LLM (OpenAI/Anthropic/DeepSeek), returns `BaseChatModel`
- `app/prompts/loader.py` — YAML-based prompt template loader (`intent.yaml`, `summarize.yaml`)

**Subscription & Chat Management:**
- `app/subscription/handler.py` — subscribe/unsubscribe/list commands, vendor aliases (12+ mappings), command regex detection, push time/frequency preferences. Constant: `ALL_VENDORS`, `PUSH_TIMES` (09:00/12:00/18:00), `FREQUENCIES` (daily/weekdays/weekly_monday)
- `app/chat/lifecycle.py` — chat auto-registration on bot added, deactivation on bot removed, active chat queries, owner_id caching, permission check (`can_manage_subscription()`)

**State definitions** are in `app/graph/state.py` — `PushState` (raw_url, raw_content, vendor, title, published_at, summary_points, card_json, status) and `QueryState` (user_id, chat_id, user_query, parsed_intent, query_results, reply_card_json) TypedDicts.

**Error handling:** 3x exponential backoff on scraping, LangGraph node-level retry for 429 Rate Limit, auto-refreshing Feishu token in Redis. URL marked processed only after successful card send (or when zero subscribers, to prevent infinite reprocessing).

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

All 7 tasks are complete. Current test suite: **165 tests passing** across 12 test files.

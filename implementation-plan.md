> **状态：已完成（2026-08-08），实际实现超出 v1 范围**
> 本计划定义 7 个 Task 已全部交付。后续新增功能（未在计划中）：
> Twitter/Nitter 支持、订阅系统、推送时间/频率定制、Bot 自动发现、权限控制、双阶段推送。
> 最新架构见 [CLAUDE.md](./CLAUDE.md)。

---

#### 2. 实施计划文档：`2026-08-07-feishu-ai-news-bot.md`

```markdown
# Feishu AI News Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个基于 FastAPI、LangGraph 和飞书 Open API 的 AI 新闻推送与交互查询机器人，支持定时轮询抓取、大模型智能提炼摘要、生成飞书富文本卡片推送，以及群内 `@Bot` 历史动态检索。

**Architecture:** 项目采用模化架构，包含 FastAPI 网关、APScheduler 调度器、Redis 缓存与防重过滤、MySQL 持久化存储。通过 LangGraph 的 `NewsPushGraph` 与 `BotQueryGraph` 分别编排后台抓取推送与前端交互查询两条状态链路。

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, LangChain Core, Pydantic v2, SQLAlchemy, Redis-py, APScheduler, Trafilatura, Pytest, Docker.

## Global Constraints

- Python 版本必须大于等于 3.10。
- 全部配置通过 `pydantic-settings` 统一读取管理。
- 代码使用 TDD 模式开发，每个任务均包含具体的单测用例与 pytest 验证步骤。
- 飞书消息统一采用 Interactive Card (富文本 JSON) 格式。
- **一期范围**：仅 RSS 数据源。社交媒体/公众号监控为后续版本。
- **推送目标**：通过环境变量 `FEISHU_CHAT_IDS`（逗号分隔的 chat_id 列表）固定配置，不实现动态订阅。
- **轮询频率**：每天一次（APScheduler cron 表达式 `0 9 * * *`，上午 9:00）。
- **LLM**：支持多厂商切换（OpenAI / Anthropic / DeepSeek），通过 `app/llm/provider.py` Factory 统一封装。
- **代码规范**：注释使用中文，变量名/函数名/类名使用英文。

---

## File Structure & Map

- `app/core/config.py`: 环境参数配置
- `app/core/security.py`: 飞书 Webhook 签名验证
- `app/db/database.py`: SQLAlchemy 初始化与工具函数
- `app/db/models.py`: 新闻表 ORM 模型
- `app/db/redis.py`: Redis 客户端，含防重与 Token 缓存
- `app/feishu/client.py`: 飞书 Open API 封装
- `app/feishu/card_builder.py`: 飞书富文本卡片构造器
- `app/fetcher/rss_fetcher.py`: RSS 订阅拉取
- `app/fetcher/web_scraper.py`: 网页正文提取
- `app/graph/state.py`: LangGraph State 状态定义
- `app/graph/nodes/extract.py`: 抓取正文节点
- `app/graph/nodes/summarize.py`: LLM 摘要节点
- `app/graph/nodes/store.py`: 数据持久化节点
- `app/graph/nodes/intent.py`: 意图识别节点
- `app/graph/news_push_graph.py`: 推送工作流定义
- `app/graph/bot_query_graph.py`: 查询工作流定义
- `app/llm/provider.py`: LLM Factory，统一封装 OpenAI/Anthropic/DeepSeek 等
- `app/scheduler/jobs.py`: 定时轮询调度任务
- `app/main.py`: FastAPI 应用入口与飞书 Event 接收

---

### Task 1: 项目基础结构与核心配置模块

**Files:**
- Create: `requirements.txt`, `.env.example`, `app/__init__.py`, `app/core/__init__.py`, `app/core/config.py`
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Environment variables
  - `FEISHU_APP_ID`, `FEISHU_APP_SECRET` — 飞书应用凭证
  - `FEISHU_CHAT_IDS` — 逗号分隔的推送目标 chat_id 列表
  - `LLM_PROVIDER` — 当前使用的 LLM 厂商（`openai` | `anthropic` | `deepseek`）
  - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY` — 各厂商独立 API Key
  - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
  - `REDIS_HOST`, `REDIS_PORT`
- Produces: `Settings` object via `pydantic-settings BaseSettings`

- [ ] **Step 1: 编写配置模块失败测试**
- [ ] **Step 2: 运行测试并确认失败**
- [ ] **Step 3: 编写配置模块实现与依赖文件**
- [ ] **Step 4: 运行测试并确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 2: 数据库持久化层与 Redis 防重

**Files:**
- Create: `app/db/database.py`, `app/db/models.py`, `app/db/redis.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Settings` from `app.core.config`
- Produces: `NewsArticle` ORM class, `get_db_session()`, `RedisClient` class with:
  - `is_url_processed(url) -> bool` — 检查 URL 是否已在 Redis Set 中
  - `mark_url_processed(url)` — 将 URL 加入 Redis Set
  - `cache_token(token)` / `get_cached_token() -> str | None` — 飞书 token 缓存

- [ ] **Step 1: 编写数据库与 Redis 单元测试**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现数据库模型与 Redis 客户端**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 3: 飞书 SDK 封装与富文本卡片构造器

**Files:**
- Create: `app/core/security.py`, `app/feishu/client.py`, `app/feishu/card_builder.py`
- Test: `tests/test_feishu.py`

**Interfaces:**
- Consumes: `Settings`, `RedisClient`
- Produces:
  - `FeishuClient.send_card(receive_id, card_json)` — 发送卡片到单个目标
  - `FeishuClient.send_card_to_all(card_json)` — 遍历 `FEISHU_CHAT_IDS` 批量发送
  - `build_news_card(title, vendor, summary_points, raw_url, published_at) -> dict` — 构建飞书卡片 JSON
  - `verify_signature(timestamp, nonce, body, signature) -> bool` — webhook 签名校验

- [ ] **Step 1: 编写飞书卡片与 Client 测试**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现卡片构造器与 飞书 API Client**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 4: 新闻抓取器 (RSS + Web Scraper)

**Files:**
- Create: `app/fetcher/__init__.py`, `app/fetcher/rss_fetcher.py`, `app/fetcher/web_scraper.py`
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Consumes: RSS feed URLs（从配置中读取列表）和网页 URL
- Produces:
  - `fetch_rss_items(rss_url) -> List[dict]` — 解析 RSS，返回 `[{title, url, published_at}, ...]`
  - `scrape_article_text(url) -> str` — 基于 Trafilatura 提取网页正文
- 一期仅实现 RSS 抓取；社交媒体/公众号监控为后续任务

- [ ] **Step 1: 编写抓取器测试用例**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现抓取逻辑**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 5: LLM Provider 抽象层 + LangGraph NewsPushGraph

**Files:**
- Create: `app/llm/__init__.py`, `app/llm/provider.py`, `app/graph/__init__.py`, `app/graph/state.py`, `app/graph/nodes/__init__.py`, `app/graph/nodes/extract.py`, `app/graph/nodes/summarize.py`, `app/graph/nodes/store.py`, `app/graph/news_push_graph.py`
- Test: `tests/test_llm_provider.py`, `tests/test_news_push_graph.py`

**Interfaces:**
- `get_llm() -> BaseChatModel` — Factory 根据 `LLM_PROVIDER` 配置返回对应 ChatModel
- Consumes: Raw URL and vendor metadata
- Produces: 可执行的 `push_graph` workflow，编排抓取→总结→存储→推卡全流程
- **批量处理**：调度器逐篇调用 Graph，单篇文章失败不影响其他文章

- [ ] **Step 1: 编写 NewsPushGraph 节点单元测试**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现 State 与 NewsPushGraph**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 6: LangGraph 交互查询图 (`BotQueryGraph`)

**Files:**
- Create: `app/graph/nodes/intent.py`, `app/graph/bot_query_graph.py`
- Test: `tests/test_bot_query_graph.py`

**Interfaces:**
- Consumes: User text query from Feishu `@Bot`
- Produces: Parsed search intent and response card JSON

- [ ] **Step 1: 编写意图识别与查询 Graph 测试**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现 Intent 识别与 Query Workflow**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**

---

### Task 7: APScheduler 调度器与 FastAPI 入口整合

**Files:**
- Create: `app/scheduler/jobs.py`, `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: FastAPI Webhook endpoint `/feishu/events` and scheduler background jobs.
- Produces: Running FastAPI service handling Feishu Bot webhooks & daily scheduled news polling (APScheduler cron: `0 9 * * *`).

- [ ] **Step 1: 编写 FastAPI 路由集成测试**
- [ ] **Step 2: 运行测试确认失败**
- [ ] **Step 3: 实现调度任务与 FastAPI 入口**
- [ ] **Step 4: 运行测试确认通过**
- [ ] **Step 5: 提交代码**
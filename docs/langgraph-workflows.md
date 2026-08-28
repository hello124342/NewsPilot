# LangGraph Workflow 架构

本文描述项目中两个 LangGraph 工作流的节点、状态流转、触发入口和外部依赖。工作流定义位于 `app/graph/`，状态模型位于 `app/graph/state.py`。

## 1. 总体架构

```mermaid
flowchart TB
    subgraph Ingress[事件与任务入口]
        Scheduler[APScheduler<br/>RSS 定时任务]
        Feishu[Feishu WebSocket]
        Telegram[Telegram Webhook]
        Discord[Discord Gateway]
    end
    subgraph Graphs[LangGraph 工作流]
        Push[NewsPushGraph<br/>文章抓取与推送]
        Query[BotQueryGraph<br/>自然语言查询与问答]
    end
    subgraph Services[共享服务]
        MySQL[(MySQL<br/>文章与订阅)]
        Redis[(Redis<br/>缓存、幂等与队列)]
        Chroma[(ChromaDB<br/>向量索引)]
        LLM[LLM Provider<br/>OpenAI / Anthropic / DeepSeek]
        IntentModel[Ollama LoRA<br/>list / qa / unknown]
        Adapters[Platform Adapters<br/>Feishu / Telegram / Discord]
    end
    Scheduler --> Push
    Feishu --> Query
    Telegram --> Query
    Discord --> Query
    Push --> MySQL
    Push --> Chroma
    Push --> Redis
    Push --> LLM
    Push --> Adapters
    Query --> MySQL
    Query --> Chroma
    Query --> Redis
    Query --> LLM
    Query --> IntentModel
    Query --> Adapters
```

订阅、帮助、设置等命令在事件路由层直接处理，不进入 `BotQueryGraph`。只有自然语言查询会构造 `QueryState` 并调用 `graph.invoke(state)`。

## 2. NewsPushGraph

入口是 `app/main.py::process_rss_job`，每篇未处理文章创建一个 `PushState`。默认完整推送模式的流程如下：

```mermaid
flowchart LR
    Start([PushState: PENDING]) --> Extract[extract<br/>RSS 摘要优先，否则抓取正文]
    Extract -->|status != FAILED| Summarize[summarize<br/>LLM 生成 3 条要点]
    Extract -->|FAILED| End([END])
    Summarize -->|status != FAILED| Store[store<br/>写入 MySQL，并尝试写入 ChromaDB]
    Summarize -->|FAILED| End
    Store -->|status != FAILED| Card[build_card<br/>构造 Feishu Card JSON]
    Store -->|FAILED| End
    Card -->|status != FAILED| Send[send_feishu<br/>按订阅筛选目标并发送]
    Card -->|FAILED| End
    Send --> End
```

`build_push_graph(push_enabled=False)` 用于预处理，只执行 `extract -> summarize -> store -> END`。启用 `enable_checkpoint=True` 时使用 `MemorySaver`；启用 `enable_human_review=True` 时会在 `send_feishu` 前中断，等待人工恢复。

## 3. BotQueryGraph

查询状态为 `QueryState`，入口节点 `intent_router` 将用户输入分为 `list`、`qa` 或 `unknown`。意图识别采用简单的两级策略：先检查明确的 AI 新闻关键词和查询信号；未命中时再调用可选的本地 Ollama LoRA 小模型。模型关闭、调用异常、JSON 格式无效或置信度低于 `0.75` 时统一进入 `unknown`。

```mermaid
flowchart TD
    Start([QueryState: user_query]) --> Router[intent_router<br/>规则优先，未命中调用 Ollama LoRA]
    Router -->|list| Intent[intent<br/>解析 vendor 与 days]
    Intent --> Search[search_db<br/>MySQL 查询最近文章]
    Search --> Format[format_response<br/>生成 RichMessage 与兼容 Card]
    Router -->|qa| Retrieve[rag_retrieve<br/>Query Embedding + ChromaDB top-K]
    Retrieve --> Answer[rag_answer<br/>基于文章上下文生成回答]
    Answer --> RagFormat[format_rag_response<br/>生成回答、来源与按钮]
    Router -->|unknown| Unknown[unknown_response<br/>返回使用引导]
    Format --> Reply[reply<br/>按 platform 选择 Adapter 发送]
    RagFormat --> Reply
    Unknown --> Reply
    Reply --> End([END])
```

`list` 用于查询 AI 新闻、最新动态或新闻列表；`qa` 用于针对 AI 新闻或文章内容提问、解释和分析。`unknown` 只执行 `unknown_response`，返回新闻 Bot 使用引导，不进入 MySQL 查询或 ChromaDB 检索。

RAG 检索结果为空时生成友好兜底回复；LLM 熔断或调用失败时返回检索到的文章列表，避免整个查询失败。`reply` 统一消费 `rich_message`，并根据 `platform` 选择对应适配器。

### 3.1 意图识别状态

| 字段 | 含义 |
|------|------|
| `query_type` | 最终路由：`list`、`qa` 或 `unknown` |
| `intent_confidence` | 规则命中为 `1.0`；模型结果为模型返回值；失败时为 `0.0` |
| `intent_source` | `rule`、`ollama` 或具体失败原因，如 `ollama_error`、`ollama_low_confidence` |

Ollama 配置位于 `.env`：`INTENT_OLLAMA_ENABLED`、`INTENT_OLLAMA_URL`、`INTENT_OLLAMA_MODEL`、`INTENT_CONFIDENCE_THRESHOLD` 和 `INTENT_OLLAMA_TIMEOUT_SECONDS`。默认关闭本地模型；完成模型创建和离线验证后，设置 `INTENT_OLLAMA_ENABLED=true`。

## 4. 状态与扩展约束

- `PushState` 负责在抓取、摘要、持久化、卡片和发送节点之间传递文章数据及 `status`。
- `QueryState` 负责传递用户信息、路由结果、数据库结果、RAG 上下文和最终消息。
- 新增节点时应明确读写哪些 State 字段，并在图构建函数中注册边；失败场景应设置 `status` 或提供可见的降级结果。
- 外部服务调用应沿用现有缓存、熔断、重试和指标埋点模式。

## 5. 相关代码与测试

- `app/graph/news_push_graph.py`
- `app/graph/bot_query_graph.py`
- `app/graph/nodes/intent_router.py`
- `app/graph/nodes/unknown_response.py`
- `app/graph/state.py`
- `app/intent/ollama_classifier.py`
- `tests/test_news_push_graph.py`
- `tests/test_bot_query_graph.py`
- `tests/test_intent_router.py`
- `tests/test_ollama_classifier.py`
- `tests/test_integration.py`

运行 `pytest -v tests/test_intent_router.py tests/test_ollama_classifier.py tests/test_bot_query_graph.py` 可验证三分类路由、Ollama 响应解析和 `unknown` 图路径；运行 `pytest -v -k "graph or integration"` 可验证图编译和主要集成路径。

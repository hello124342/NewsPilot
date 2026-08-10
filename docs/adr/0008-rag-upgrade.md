# ADR 0008: RAG 智能问答升级

- **状态**：已批准 (Approved)
- **日期**：2026-08-09
- **决策者**：LTJ

## 背景

Bot 当前的 @Bot 查询仅支持"厂商 + 日期 → SQL 搜索 → 返回文章列表"模式。用户问 "GPT-5 什么时候发布" 时，只能按关键词模糊匹配后返回卡片列表，无法真正回答问题。

**核心需求**：将 Bot 从"AI 新闻聚合器"升级为"AI 行业情报分析师"。用户用自然语言问问题，Bot 从历史文章中语义检索相关内容，由 LLM 综合生成带引用的答案。

## 决策

### 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| **向量库** | ChromaDB (PersistentClient) | pip install 即用，零运维，本地文件持久化 |
| **Embedding** | OpenAI text-embedding-3-small (1536-dim) | $0.02/1M tokens，复用现有 API key，精度高 |
| **原文存储** | MySQL `raw_content` TEXT 列 | 与现有 ORM 一致，检索命中后 JOIN 回填 |
| **检索策略** | 标题+摘要 embed → ChromaDB top-K → MySQL 回填 raw_content | 摘要质量高（LLM 生成），检索精度好 |
| **意图路由** | 三分类 LLM + 关键词降级 | 向后兼容现有 list 路径 + 新增 qa 路径 |
| **Embedding时机** | store_node 入库后立即 embed | 复用现有 pipeline，不改调度 |

### 架构：条件路由双路径

```
[Start] → IntentRouterNode (LLM 3-class + keyword fallback)
              ├─ "list" → IntentNode → SearchDBNode → FormatResponseNode → Reply → [End]
              └─ "qa"   → RAGRetrieveNode → RAGAnswerNode → FormatRAGResponseNode → Reply → [End]
```

### 卡片设计

- **List 路径**：保持现有 `build_news_card()` 蓝色 Header 卡片
- **QA 路径**：新增 `build_rag_answer_card()` 绿色 Header 卡片：
  - Header: 🤖 AI 行业情报
  - Body: 💬 用户问题 → LLM 回答正文（含 `[来源 N]` 引用）
  - Footer: 📚 参考来源按钮（链接原文）

## 新增文件

| 文件 | 用途 |
|------|------|
| `app/rag/embedder.py` | OpenAI embedding 封装（3x retry + Prometheus metrics） |
| `app/rag/vector_store.py` | ChromaDB PersistentClient CRUD |
| `app/graph/nodes/intent_router.py` | 意图路由节点（list/qa/command 三分类） |
| `app/graph/nodes/rag_retrieve.py` | 语义检索节点（query embed → ChromaDB top-K → MySQL 回填） |
| `app/graph/nodes/rag_answer.py` | 答案生成节点（LLM 阅读上下文 → 含引用回答） |
| `app/prompts/rag_answer.yaml` | RAG 答案 Prompt 模板 |
| `tests/test_rag.py` | RAG 模块测试（22 tests） |
| `tests/test_intent_router.py` | 意图路由测试（9 tests） |

## 修改文件

| 文件 | 改动 |
|------|------|
| `app/db/models.py` | `NewsArticle` 新增 `raw_content = Column(Text)` |
| `app/graph/nodes/store.py` | 存储 `raw_content` + 调用 `_embed_to_chromadb()` |
| `app/graph/bot_query_graph.py` | 条件路由重构（intent_router → list/qa 分叉） |
| `app/graph/state.py` | `QueryState` 新增 `query_type`, `rag_context`, `rag_answer` |
| `app/feishu/card_builder.py` | 新增 `build_rag_answer_card()` |
| `app/feishu/event_router.py` | 修复首条消息被吞 bug（welcome card 不再阻断查询） |
| `app/core/metrics.py` | 新增 6 项 RAG 指标 |
| `requirements.txt` | 新增 `chromadb>=0.5` |

## Bug 修复（2026-08-10）

| Bug | 根因 | 修复 |
|-----|------|------|
| "最近一周 OpenAI" 返回空 | MySQL 缺少 `raw_content` 列 → SQL 报错 | 手动 `ALTER TABLE` + `database.py:_run_migrations()` 自动迁移 |
| "昨天" 查询返回空 | `timedelta(days=1)` = 24h 前，上午文章被截断 | `search_db.py:_calc_since()` → 日历日 `00:00 UTC` 边界 |
| LLM 不认识 "今天/昨天" | Prompt 缺少时间示例 + 降级 days 固定为 3 | Prompt 添加示例 + `intent.py:_extract_days_from_query()` 关键词提取 |

## 影响

- **用户**：可以用自然语言问问题（"GPT-5 什么时候发布"、"OpenAI 和 Anthropic 有什么区别"）
- **飞书卡片**：新增绿色 RAG 答案卡片，与蓝色新闻卡区分
- **性能**：每次 qa 查询增加 1 次 embedding API 调用（~200ms）+ 1 次 LLM API 调用（~3s）
- **成本**：embedding ~$0.00002/篇，LLM 回答 ~$0.001/次（deepseek-chat）
- **测试**：247 tests (17 files)，零回退

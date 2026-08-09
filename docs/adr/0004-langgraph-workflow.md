# ADR-0004: 采用 LangGraph 作为工作流引擎

**状态：** 已采纳

**日期：** 2026-08-09

**决策者：** 项目作者

---

## 背景

项目的核心业务逻辑是两条流水线：

1. **NewsPushGraph**：抓取 → 摘要 → 存储 → 建卡 → 发送（5 个顺序节点）
2. **BotQueryGraph**：解析意图 → 搜索 → 格式化 → 回复（4 个顺序节点）

这些流水线需要：
- 明确的节点执行顺序
- 失败时的短路/跳过逻辑（上游 FAILED → 跳过下游）
- 可选的中间状态持久化（checkpoint）
- 可选的人工审核断点（human-in-the-loop）

## 决策

**采用 LangGraph `StateGraph` 作为工作流引擎**，而非自研状态机。

LangGraph 是 LangChain 生态的标准工作流框架，专为 LLM 应用的 pipeline 编排设计。选择它的核心理由：

1. **业界标准**：LangGraph 是 AI 应用开发的事实标准之一，使用它体现技术选型判断力——知道什么时候该用现成方案而非重复造轮子
2. **功能完备**：checkpoint（状态持久化/恢复）、HITL（人工审核断点）、streaming（流式输出）、subgraph（子图复用）——这些功能自研成本极高
3. **与 LangChain 生态无缝集成**：项目已使用 LangChain 的 ChatOpenAI/ChatAnthropic，LangGraph 的 StateGraph 与这些组件天然兼容

具体实现：

- **`TypedDict` State**：`PushState` 和 `QueryState` 定义共享状态结构（`total=False` 使所有字段可选，支持增量修改）
- **条件边（conditional edges）**：`_should_continue()` 检查 `state["status"] == "FAILED"` 并路由到 END，实现失败短路——这是 LangGraph 相比线性代码的核心优势
- **参数化构图**：`build_push_graph(push_enabled, enable_checkpoint, enable_human_review)` 控制管道截断——同一个图定义支持两阶段管道中的不同模式
- **`MemorySaver` checkpointing**：调试阶段启用，每一步状态可回放、可恢复
- **`interrupt_before`**：在关键节点前暂停，支持人工审核后再继续执行

## 后果

### 正面

- **开发效率**：用框架而非自研——checkpoint、HITL、条件路由等能力通过配置即可启用，无需从零实现
- **可维护性**：节点是纯函数 `(State) -> State`，可独立测试和替换。新人接手时直接看图定义即可理解业务流程
- **可视化**：LangGraph 自动生成 Mermaid 图，可直接用于文档
- **类型安全**：TypedDict 状态 + 函数签名注解提供编译期检查
- **AI 应用开发的标准实践**：在简历上展示的是「选用合适的框架解决实际问题」的判断力，而非「什么都要自己写」的初级思维

### 负面

- **依赖传递**：LangGraph + LangChain Core 引入额外依赖（约 20+ 包），增加镜像体积和冷启动时间
- **调试曲线**：图执行路径运行时动态决定，不理解 conditional edges 机制的开发者可能困惑
- **版本升级风险**：LangGraph API 仍在快速迭代（0.x 版本），升级可能引入 breaking changes

## 备选方案

| 方案 | 评估 |
|------|------|
| **LangGraph（选用）** | 业界标准、功能完备、生态兼容。对 AI 应用开发岗位是加分项 |
| 自研状态机 | 零外部依赖、完全可控。但需自行实现 checkpoint/HITL/条件路由，开发量大且非核心业务——属于「造轮子」 |
| Celery / Task Queue | 适合分布式大规模场景。本项目单进程即可满足需求，引入 broker 是过度设计 |

## 决策理由总结

**「不重复造轮子」是成熟工程师的标志。** LangGraph 提供了工作流引擎所需的全部能力（条件路由、checkpoint、HITL），且是 AI 应用开发领域的主流选择。选用它意味着把精力集中在业务逻辑（节点实现）上，而非基础设施代码。

## 相关

- `app/graph/news_push_graph.py`: NewsPushGraph 构建
- `app/graph/bot_query_graph.py`: BotQueryGraph 构建
- `app/graph/state.py`: TypedDict 状态定义
- `app/graph/nodes/`: 10 个节点实现

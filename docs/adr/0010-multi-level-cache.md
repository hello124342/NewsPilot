# ADR 0010: 多级缓存架构（L1 本地 + L2 Redis）

**Date:** 2026-08-22
**Status:** Accepted
**Context:** LLM 调用（意图分类 / RAG 答案生成）是查询链路最贵的一环——延迟 1-30s、按 token 计费。
相同或相似的查询在群聊里高频重复（「OpenAI 最近有什么新闻」），却每次都重新调用 LLM。
Embedding 计算是确定性的，同一文本重复算无意义。DB 热点读（订阅列表 / 推送偏好）在每次推送和查询里反复命中同几行。

## Decision

引入统一的两级缓存组件 `app/core/multi_cache.py`：**L1 进程内 LRU+TTL** + **L2 Redis**，
覆盖三个应用点——LLM 结果、Embedding、DB 热点读——并内建缓存三大经典问题的防护。

## Rationale

### 为什么要两级而非只用 Redis？

- **L1（本地 LRU）**：纳秒级命中，无网络往返；进程内最热的 key 直接命中，扛住突发。
- **L2（Redis）**：跨进程/跨实例共享，容量大；L1 miss 时兜底，避免每个实例各自穿透到源。
- 读路径：L1 命中直接返回 → L1 miss 查 L2，命中则回填 L1 → L2 miss 才穿透到源，双写回填。

单用 Redis 每次命中都有网络往返（~1ms），单用本地则多实例间无法共享、容量受进程内存限制。两级各取所长。

### 三大防护为什么必配？

多级缓存不做防护就是把雪崩风险放大：

- **击穿（singleflight）**：热点 key 过期瞬间，N 个并发请求同时 miss → 全部穿透到 LLM，
  瞬时打爆下游。用 `threading.Lock` + per-key event 让同 key 并发只放一个请求穿透，其余等结果。
- **穿透**：查询不存在的 key（恶意或误问）每次都打到源。空结果也缓存一个短 TTL（60s）哨兵值。
- **雪崩**：大批 key 同一时刻过期 → 集体穿透。TTL 上加 ±10% 随机抖动，把过期时刻打散。

### 为什么 summarize 不缓存，但 intent/rag_answer 缓存？

- `intent_router` / `intent` / `rag_answer`：输入是**用户查询**，天然重复，命中率高 → 缓存（key = `llm:{op}:{sha256(prompt)[:16]}`，TTL 1h）。
- `summarize`：输入是**文章正文**，每篇文章只处理一次，天然不重复 → 缓存命中率为 0，纯浪费内存，不缓存。

### 为什么 Embedding TTL 24h、DB 5min？

- Embedding 是**确定性计算**（同文本永远同向量），可长缓存（24h），key = `embed:v1:{sha256(text)[:16]}`。
- DB 读是**可变数据**（订阅随时增删），只能短缓存（5min）+ **写后主动失效**：
  subscribe/unsubscribe/set_preference 后立即删对应 key，避免用户改了设置却看到旧值。

### 复用现有约定

- L1 复用 `app/core/cache.py:TTLCache`，加 `maxsize` 上限防内存无界。
- 指标 `cache_hit_total{cache,level}` / `cache_miss_total{cache}` 按 `metrics.py` 的 isolated registry 风格加，`init_metrics()` 置零。
- DB 缓存迁移原有 `chat_pref_cache`，键作用域保持 `platform:conversation_id`。

## Consequences

**Positive:**
- LLM 调用量随命中率线性下降——直接省延迟和 token 计费（命中率见 Grafana）。
- singleflight 让热点 key 过期不再引发下游尖峰。
- 统一组件，三个应用点共用一套防护，不用各写各的。

**Negative:**
- 引入缓存一致性问题：DB 写后必须记得失效，漏一处就是脏读（缓解：失效逻辑集中在 repository 写方法内）。
- L1 在多实例间不一致（各进程独立），靠短 TTL 收敛。
- Redis 不可用时 L2 整体降级为 miss，穿透压力回到源（可接受：本地 L1 仍在挡）。

**Risks:**
- 缓存 key 用 `sha256(prompt)[:16]` 截断，理论上有碰撞概率（16 hex = 64 bit，可忽略）。

## Alternatives Considered

### 只用 `functools.lru_cache`（rejected）
无 TTL、无跨进程共享、无防护，且无法主动失效。玩具级，不够。

### 只用 Redis 单级（rejected）
每次命中都有网络往返，热点场景下 Redis 自身成瓶颈；无本地兜底，Redis 抖动直接击穿。

### 引入专用缓存中间件（如 Memcached）（rejected）
项目已有 Redis，再引一个组件增加运维面。Redis 的 `setex` 足够覆盖需求。

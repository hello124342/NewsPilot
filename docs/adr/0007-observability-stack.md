# ADR-0007: 结构化日志 + Prometheus + Grafana 可观测性栈

**状态：** 已采纳

**日期：** 2026-08-10

**决策者：** 项目作者

---

## 背景

项目在本地开发和演示阶段运行，缺乏生产环境所需的可观测性能力：

1. **日志**：使用 `logging.basicConfig` 输出非结构化文本，无法被日志聚合系统（ELK/Loki）高效索引和搜索
2. **指标**：无任何运行时指标采集，系统运行状态完全不可见——RSS 抓取是否成功、LLM 调用延迟、飞书 API 错误率均无法量化
3. **可视化**：无仪表板，排查问题依赖手动查日志

## 决策

采用三层可观测性栈：

### 1. 结构化日志 — `python-json-logger`

- 替换 `logging.basicConfig` 为 JSON 格式输出
- 所有现有 `logger.info(msg)` 调用**零改动**兼容
- 日志级别通过 `LOG_LEVEL` 环境变量控制
- 第三方库噪音日志（urllib3, httpx, lark_oapi）自动抑制为 WARNING

**弃用替代方案：**
- **structlog**：需要改写所有 `logger.info()` 调用点为 `log.info("event", key=value)`，侵入性过大
- **loguru**：非标准库，与现有 `logging` 生态不兼容，且移除 `logging` 基础设施成本高

### 2. 指标采集 — `prometheus_client`

- 36 项指标覆盖 8 个领域：HTTP、RSS Pipeline、Deliver Pipeline、LLM、Feishu API、Circuit Breaker、WebSocket、Scraping
- 使用**独立 `CollectorRegistry`** 而非全局默认 registry，避免第三方库冲突
- 通过 FastAPI `GET /metrics` 端点暴露，**不另开端口**
- 埋点策略：**decorator + context manager** 优先，业务代码中避免直接写 `counter.inc()`

**指标分类：**

| 类别 | 指标数 | 类型 |
|------|--------|------|
| HTTP | 2 | Counter + Histogram |
| RSS Pipeline | 5 | Counter + Histogram + Gauge |
| Deliver Pipeline | 3 | Counter + Histogram |
| LLM Calls | 2 | Histogram + Counter |
| Feishu API | 2 | Histogram + Counter |
| Circuit Breaker | 1 | Gauge |
| WebSocket | 2 | Gauge + Counter |
| Content Scraping | 2 | Counter |

**弃用替代方案：**
- **OpenTelemetry**：功能强大但引入大量依赖（SDK + exporter + collector），对于单体应用过度设计
- **`start_http_server` 独立端口**：额外端口增加运维复杂度，不利于容器化部署

### 3. 可视化 — Grafana + 预制仪表板

- `docker-compose.yml` 新增 `prometheus` 和 `grafana` 两个服务
- Grafana 启动时自动加载数据源（Prometheus）和预制仪表板
- 仪表板含 8 行面板（KPI 概览、HTTP 流量、管道指标、推送投递、LLM 调用、WebSocket、抓取、熔断器）
- 默认时间范围：最近 6 小时，30s 自动刷新

**设计原则：**
- 仪表板通过 JSON 文件声明式配置，`docker-compose up -d` 即用
- 不依赖 Grafana UI 手动创建面板（可复现、可版本控制）

## 后果

### 正面

- **排障效率**：JSON 日志可被 `jq` 直接过滤，Prometheus 查询可定位"哪个环节出错"
- **可视化运维**：`docker-compose up -d` 后浏览器打开 `:3000` 即看到完整运行状态
- **零侵入升级**：`python-json-logger` 不改现有 `logger.info()` 调用；decorator 埋点不污染业务逻辑
- **简历亮点**：体现生产级运维意识和云原生技术栈（Prometheus 是 CNCF 毕业项目）
- **容器一体化**：Prometheus + Grafana 随 `docker-compose` 启停，无需额外部署步骤

### 负面

- **Docker 镜像体积**：增加 Prometheus（~250MB）和 Grafana（~450MB）两个容器，总计约 700MB
- **日志格式变更**：JSON 输出替代纯文本，开发者 `docker logs` 直接阅读体验略降（可通过 `| jq` 弥补）
- **指标初始化**：需要在启动时调用 `init_metrics()` 将指标设为零值，否则 Grafana 显示 "No data"
- **学习曲线**：团队需了解 PromQL 查询语法才能自定义告警规则

### 中性

- 指标采集对性能影响可忽略（`prometheus_client` 操作是内存中的原子递增）
- 监控栈仅在需要时通过 `docker-compose` 启动，不影响本地 `uvicorn` 开发流程

---

## 相关文件

| 文件 | 用途 |
|------|------|
| `app/core/metrics.py` | 指标定义 + decorator/context manager + init |
| `app/core/logging_config.py` | 结构化日志 setup 函数 |
| `app/core/config.py` | `LOG_LEVEL` 配置项 |
| `monitoring/prometheus.yml` | Prometheus 抓取配置 |
| `monitoring/grafana-datasources.yml` | Grafana 数据源 |
| `monitoring/grafana-dashboard.json` | 预制仪表板（~3KB JSON） |
| `monitoring/grafana-dashboard-provider.yml` | Grafana 面板加载器 |
| `tests/test_metrics.py` | 34 个指标单元测试 |
| `tests/test_logging_config.py` | 10 个日志配置测试 |
| `tests/test_health_detailed.py` | 6 个健康检查测试 |

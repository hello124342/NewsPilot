# ADR 0009: Multi-Platform Adapter Pattern

**Date:** 2026-08-11
**Status:** Accepted
**Context:** Feishu AI News Bot 从单一飞书平台扩展为支持 Telegram（以及未来的 Slack、Discord 等）。

## Decision

采用 **Platform Adapter 模式**——定义 `PlatformAdapter` ABC 作为跨平台消息接口，每平台一个具体适配器实现。

## Rationale

### 为什么不用 if-else 分支？

直接在各处 `if platform == "feishu": ... elif platform == "telegram": ...` 会导致：
- 核心业务逻辑被平台差异污染
- 新增平台需要修改多处代码（散弹式修改）
- 测试复杂度指数增长

Adapter 模式将平台差异**隔离在单一实现文件中**，核心逻辑只依赖抽象接口。

### 为什么选择这个接口粒度？

```python
class PlatformAdapter(ABC):
    def send_message(conversation_id, message: RichMessage) -> dict
    def get_conversation_info(conversation_id) -> ConversationInfo | None
    def get_platform_name() -> str
    def get_platform_label() -> str
```

只抽象了**消息发送**和**对话信息查询**——这两个是各平台的核心差异点。事件接收（WebSocket vs Webhook）差异太大，不适合统一抽象，放在各平台的事件处理模块中。

### 为什么用 RichMessage 作为中间格式？

飞书用结构化 Interactive Card JSON（`tag: "div"`, `lark_md` 等私有标签），Telegram 用 HTML + InlineKeyboard，Slack 用 Block Kit JSON。

`RichMessage` 是最小公约数：
- `body` — Markdown/HTML 文本（所有平台都支持某种富文本）
- `title` — 可选标题
- `buttons` — 链接或回调按钮（所有平台都有）
- `color_hint` — 颜色映射（各平台自行解释）

具体渲染由各平台的 renderer 完成：`RichMessage → 飞书 Card JSON` / `→ Telegram HTML + Keyboard`。

### 为什么复刻 LLM Provider Factory 模式？

`app/llm/provider.py` 的 `get_llm()` 已是项目中成熟的 Factory 模式。`app/platforms/registry.py` 的 `get_platform_adapter()` 完全复刻同一模式：
- 字符串 key → 具体类
- 延迟导入避免循环依赖
- 配置缺失时优雅降级（返回 None 而非崩溃）
- 注册表集中管理所有平台

### 为什么 chat_id 迁移采用向后兼容策略？

`chat_id` 贯穿全系统（4表 + 所有 repository 方法签名 + graph state），一次性全局修改变更面太广。采用渐进式：
1. 加 `platform` + `conversation_id` 列（DEFAULT 保证现有数据不受影响）
2. 自动迁移回填：`UPDATE ... SET conversation_id = chat_id`（仅空行）
3. 保留 `chat_id` 列做兼容查询
4. 新代码用 `(platform, conversation_id)`，旧代码继续用 `chat_id`（默认 platform="feishu"）

## Consequences

**Positive:**
- 新增平台只需实现一个适配器类 + renderer + event handler，核心逻辑零修改
- 各平台故障隔离——Telegram API 挂了不影响飞书推送
- 247 测试零回归，飞书功能完全不受影响

**Negative:**
- 增加了一层间接性（adapter → renderer → platform SDK）
- `RichMessage` 只能表达各平台的公共子集，飞书的高级 Card 交互（如多列布局）无法在 Telegram 上复现
- 数据模型迁移增加了 `platform` 列的存储和查询开销（每行多 32 字节）

**Risks:**
- 未来如果某平台需要 `RichMessage` 无法表达的独有功能，可能需要扩展模型或引入平台特定 escape hatch
- Repository 的 `platform` 默认参数为 `"feishu"`——如果调用方忘记传正确的 platform，数据会被错误归类。缓解措施：Telegram 入口点（webhook、命令处理器）有明确的 `PLATFORM = "telegram"` 常量

## Alternatives Considered

### 每平台独立部署（rejected）
每个平台各部署一个 bot 实例，共享同一个数据库。优点是完全隔离，缺点是运维成本翻倍、订阅数据需要平台字段区分。

### Feature Flag 分支（rejected）
在现有代码中加 `if platform == "telegram"` 分支。短期最快，但长期维护成本高。不选。

### Webhook 统一抽象（rejected）
将飞书 WebSocket 和 Telegram Webhook 统一到一个 EventSource 接口。两个事件模型差异太大（推送 vs 拉取、事件类型、认证方式），强行统一得不偿失。

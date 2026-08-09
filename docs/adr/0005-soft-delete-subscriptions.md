# ADR-0005: 软删除 over 硬删除 for 订阅记录

**状态：** 已采纳

**日期：** 2026-08-09

**决策者：** 项目作者

---

## 背景

飞书 Bot 的厂商订阅管理需要支持用户反复订阅/退订。当用户退订一个厂商后，系统需要处理以下场景：

1. 用户可能重新订阅同一个厂商
2. 管理员可能需要查看历史订阅记录用于分析
3. 退订后立刻重新订阅不应产生数据不一致

传统的「硬删除」直接从数据库中移除记录，简单直接但丢失历史信息。保留「软删除」通过 `is_active` 标记状态，保留数据但增加查询复杂度。

## 决策

**使用软删除（`is_active` 布尔字段），在退订时标记 `is_active=False` 而非删除记录。**

具体实现 (`app/db/models.py` 中的 `Subscription` 表)：
```python
class Subscription(Base):
    __tablename__ = "subscriptions"
    ...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

重新订阅时检查已存在记录：
```python
existing = session.query(Subscription).filter_by(chat_id=..., vendor=...).first()
if existing and not existing.is_active:
    existing.is_active = True   # 恢复而非新建
    session.commit()
```

## 后果

### 正面

- **可恢复性**：退订是可逆操作，用户重新订阅时无需重新创建记录
- **数据审计**：保留完整的订阅/退订历史（可通过 `created_at` 和 `updated_at` 追踪）
- **避免唯一约束冲突**：无 `(chat_id, vendor)` 唯一索引，允许同一组合出现多次（记录历史），查询时通过 `is_active=True` 过滤
- **用户体验**：重新订阅后保留原有的时间戳，可展示「你从 8 月 1 日起订阅」

### 负面

- **查询必须加 `is_active=True` 过滤**：忘记加此条件会返回已退订的记录，导致数据错误（通过 Repository 模式封装避免——见 Repository ABC 改造）
- **存储膨胀**：历史记录永不删除，长期运行后 subscriptions 表可能变大。可通过定期归档解决（当前规模下可忽略）
- **无硬删除机制**：当前没有「彻底删除数据」的 API。如果用户要求 GDPR 数据删除（极少场景），需要新增处理

## 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| 硬删除 | 查询简单、存储小 | 丢失历史、难以恢复 |
| **软删除（选用）** | 可恢复、可审计 | 查询需过滤、存储膨胀 |
| 事件溯源 | 最完整的历史 | 实现复杂、过度设计 |

## 相关

- `app/db/models.py`: `Subscription.is_active` 字段定义
- `app/subscription/handler.py`: `subscribe()` / `unsubscribe()` 的软删除逻辑
- `app/db/repositories.py`: Repository ABC 封装 `is_active` 过滤

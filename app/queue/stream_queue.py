"""Redis Stream 可靠消息队列（用于推送投递链路）

为什么推送链路需要队列而查询链路不需要：见 ADR-0011。核心：推送是给数百个
chat 发卡片的批量任务，进程半路崩溃 = 漏发且无法追溯；at-least-once + 重试 +
死信是刚需。查询链路用户等在线上，丢了重问即可，反而不适合 at-least-once。

Redis Stream 提供的能力：
- **持久化**：XADD 写入的消息在进程崩溃后仍在 stream 中
- **consumer group**：XREADGROUP 让多消费者协作分摊、各消息只投递一次
- **故障重投**：XAUTOCLAIM 接管 consumer 崩溃后未 ACK 的 pending 消息
- **削峰**：MAXLEN 近似裁剪，防止 stream 无界增长
- **死信**：超过最大重试的消息转入 DLQ stream，人工排查而非无限重投

消息体为纯数据（article_id + 路由信息），消费侧按 article_id 查库渲染——消息瘦身，
不塞卡片 JSON。幂等去重锁（SET NX）保证 at-least-once 下重投不会让用户收到重复卡片。
"""
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Stream / group / DLQ 命名
STREAM_KEY = "feishu_bot:deliver_stream"
GROUP_NAME = "deliver_workers"
DLQ_KEY = "feishu_bot:deliver_dlq"
# 幂等去重锁前缀：sent:{article_id}:{platform}:{conversation_id}
DEDUP_PREFIX = "feishu_bot:sent"


class StreamQueue:
    """Redis Stream 封装：入队 / 消费 / ACK / 重投 / 死信 / 深度

    线程安全由 redis-py 连接池保证；每个方法都是独立的 Redis 命令。
    Redis 不可用时方法抛异常，由调用方决定降级（生产者回退内联发送）。
    """

    def __init__(
        self,
        client,
        stream_key: str = STREAM_KEY,
        group: str = GROUP_NAME,
        dlq_key: str = DLQ_KEY,
        maxlen: int = 10000,
        max_retry: int = 3,
        dedup_ttl: int = 86400,
    ):
        self._r = client
        self.stream_key = stream_key
        self.group = group
        self.dlq_key = dlq_key
        self.maxlen = maxlen
        self.max_retry = max_retry
        self.dedup_ttl = dedup_ttl
        self._ensure_group()

    def _ensure_group(self) -> None:
        """创建 consumer group（幂等：已存在则忽略 BUSYGROUP）"""
        try:
            # mkstream=True：stream 不存在时一并创建
            self._r.xgroup_create(self.stream_key, self.group, id="0", mkstream=True)
            logger.info(f"StreamQueue: created group '{self.group}' on '{self.stream_key}'")
        except Exception as e:
            # BUSYGROUP Consumer Group name already exists → 正常
            if "BUSYGROUP" in str(e):
                return
            logger.warning(f"StreamQueue: xgroup_create failed: {e}")
            raise

    # ---- 生产者 ----

    def enqueue(self, payload: dict) -> str:
        """入队一条推送消息（XADD），返回消息 ID。

        payload 为纯数据 dict，值会被 JSON 序列化进单个 "data" field。
        """
        data = json.dumps(payload, ensure_ascii=False)
        msg_id = self._r.xadd(
            self.stream_key,
            {"data": data},
            maxlen=self.maxlen,
            approximate=True,  # ~ 近似裁剪，性能更好
        )
        return msg_id

    # ---- 消费者 ----

    def consume(self, consumer: str, block_ms: int = 5000, count: int = 10) -> list[tuple[str, dict]]:
        """从 group 读取新消息（XREADGROUP，">" 表示未投递的新消息）。

        Returns: [(msg_id, payload_dict), ...]，超时无消息返回 []。
        """
        resp = self._r.xreadgroup(
            self.group,
            consumer,
            {self.stream_key: ">"},
            count=count,
            block=block_ms,
        )
        if not resp:
            return []
        # resp: [(stream_key, [(msg_id, {field: value}), ...])]
        _, messages = resp[0]
        return [(mid, self._parse(fields)) for mid, fields in messages]

    def ack(self, msg_id: str) -> None:
        """确认消息处理完成：XACK + XDEL（stream 里不再保留已处理消息）"""
        self._r.xack(self.stream_key, self.group, msg_id)
        self._r.xdel(self.stream_key, msg_id)

    def claim_stale(self, consumer: str, min_idle_ms: int = 60000, count: int = 20) -> list[tuple[str, dict]]:
        """接管超时未 ACK 的 pending 消息（XAUTOCLAIM），用于 consumer 崩溃恢复。

        对每条重投消息累加 retry_count；超过 max_retry 的转入 DLQ 并 ACK 原消息。
        Returns: 需要重新处理的 [(msg_id, payload), ...]（不含已进 DLQ 的）。
        """
        try:
            # XAUTOCLAIM stream group consumer min-idle-time start
            result = self._r.xautoclaim(
                self.stream_key,
                self.group,
                consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=count,
            )
        except Exception as e:
            logger.warning(f"StreamQueue: xautoclaim failed: {e}")
            return []

        # redis-py 返回 (next_cursor, [(msg_id, fields), ...]) 或额外的 deleted 列表
        claimed = result[1] if len(result) >= 2 else []
        out: list[tuple[str, dict]] = []
        for mid, fields in claimed:
            if not fields:  # 已被删除的残留 pending 项
                self._r.xack(self.stream_key, self.group, mid)
                continue
            payload = self._parse(fields)
            payload["retry_count"] = int(payload.get("retry_count", 0)) + 1
            if payload["retry_count"] > self.max_retry:
                self._to_dlq(mid, payload)
            else:
                out.append((mid, payload))
        return out

    def _to_dlq(self, msg_id: str, payload: dict) -> None:
        """超过重试上限：转入死信队列并 ACK 原消息（停止重投）"""
        try:
            self._r.xadd(self.dlq_key, {"data": json.dumps(payload, ensure_ascii=False)},
                         maxlen=self.maxlen, approximate=True)
        finally:
            self._r.xack(self.stream_key, self.group, msg_id)
            self._r.xdel(self.stream_key, msg_id)
        logger.error(
            f"StreamQueue: message → DLQ after {payload.get('retry_count')} retries: "
            f"article={payload.get('article_id')} target={payload.get('platform')}/{payload.get('conversation_id')}"
        )

    # ---- 幂等去重 ----

    def acquire_send_lock(self, article_id, platform: str, conversation_id: str) -> bool:
        """抢占发送锁（SET NX EX）：抢到才发送。

        at-least-once 语义下，同一消息可能被重投多次；发送前抢锁保证
        每个 (article, platform, chat) 只发一次。返回 True 表示抢到、应发送。
        """
        key = f"{DEDUP_PREFIX}:{article_id}:{platform}:{conversation_id}"
        # nx=True 仅当 key 不存在时设置；返回 True/None
        return bool(self._r.set(key, "1", nx=True, ex=self.dedup_ttl))

    # ---- 可观测 ----

    def depth(self) -> int:
        """stream 当前长度（XLEN）"""
        try:
            return int(self._r.xlen(self.stream_key))
        except Exception:
            return 0

    def pending(self) -> int:
        """group 已投递未 ACK 的消息数（XPENDING summary）"""
        try:
            info = self._r.xpending(self.stream_key, self.group)
            # redis-py: {"pending": N, ...} 或 tuple 形式
            if isinstance(info, dict):
                return int(info.get("pending", 0))
            return int(info[0]) if info else 0
        except Exception:
            return 0

    # ---- 内部 ----

    @staticmethod
    def _parse(fields: dict) -> dict:
        """解析 XADD 存入的 {"data": json} → dict"""
        raw = fields.get("data") if isinstance(fields, dict) else None
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}


# ========== 模块级单例（懒加载） ==========

_queue: Optional[StreamQueue] = None


def get_stream_queue() -> Optional[StreamQueue]:
    """获取共享 StreamQueue 实例；Redis 不可用时返回 None（调用方降级内联发送）"""
    global _queue
    if _queue is not None:
        return _queue
    try:
        import redis
        from app.core.config import Settings
        settings = Settings()  # type: ignore[call-arg]
        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        client.ping()
        _queue = StreamQueue(
            client,
            maxlen=settings.DELIVER_STREAM_MAXLEN,
            max_retry=settings.DELIVER_MAX_RETRY,
            dedup_ttl=settings.DELIVER_DEDUP_TTL,
        )
        return _queue
    except Exception as e:
        logger.warning(f"get_stream_queue: Redis unavailable, delivery will fall back inline: {e}")
        return None


def reset_stream_queue_for_tests() -> None:
    """重置模块级单例（测试用）"""
    global _queue
    _queue = None

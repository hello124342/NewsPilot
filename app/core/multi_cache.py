"""两级缓存组件（L1 进程内 LRU+TTL → L2 Redis）

统一的读穿透缓存，用于 LLM 结果 / Embedding / DB 热点读三类场景：

- **L1（进程内）**：OrderedDict 实现的 LRU + TTL，容量有上限（防内存无界），
  命中零网络开销
- **L2（Redis）**：跨进程共享、进程重启不失效；Redis 不可用时自动降级为
  L1-only（缓存永远不能成为故障放大器）
- **击穿防护（singleflight）**：同 key 并发 miss 时只放一个线程穿透到数据源，
  其余线程等待其结果——防止热点 key 过期瞬间大量相同 LLM 调用打出去
- **穿透防护**：数据源返回 None 时缓存短 TTL 哨兵，防止不存在的 key 反复查源
- **雪崩防护**：TTL 加 ±10% 随机抖动，避免同批写入的 key 同时过期

线程安全：L1 与 singleflight 状态由 threading.Lock 保护，可从查询池 worker /
WS 线程 / APScheduler 线程并发访问。

值类型约束：必须可 JSON 序列化（str / list / dict / None），L2 以 JSON 存储。
"""

import json
import logging
import random
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)

# 内部哨兵：区分"缓存未命中"与"缓存了 None"
_MISS = object()

# 空值哨兵在 L2 的 JSON 表示 / 空值缓存的短 TTL（穿透防护）
_NULL_TTL = 60

# Redis 故障后的熔断窗口：期间跳过 L2，避免每次缓存操作都等一次连接超时
_REDIS_RETRY_INTERVAL = 30.0


# ========== 共享 Redis 连接（懒加载，带短超时） ==========

_redis_lock = threading.Lock()
_redis_client = None
_redis_down_until = 0.0  # monotonic 时间戳，之前视为 Redis 不可用


def _get_redis():
    """获取共享 Redis 客户端（懒加载）

    socket 超时必须短：缓存是加速层，等 Redis 的时间不能超过省下的时间。
    连接失败返回 None，调用方降级为 L1-only。
    """
    global _redis_client, _redis_down_until
    if time.monotonic() < _redis_down_until:
        return None
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis
            settings = Settings()  # type: ignore[call-arg]
            client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            client.ping()
            _redis_client = client
            return _redis_client
        except Exception as e:
            logger.warning(f"multi_cache: Redis unavailable, L1-only mode: {e}")
            _redis_down_until = time.monotonic() + _REDIS_RETRY_INTERVAL
            return None


def _mark_redis_down() -> None:
    """L2 操作失败时调用：进入熔断窗口并丢弃连接"""
    global _redis_client, _redis_down_until
    _redis_down_until = time.monotonic() + _REDIS_RETRY_INTERVAL
    _redis_client = None


# ========== 指标（延迟导入避免循环依赖） ==========

def _emit_hit(cache: str, level: str) -> None:
    try:
        from app.core.metrics import cache_hit_total
        cache_hit_total.labels(cache=cache, level=level).inc()
    except ImportError:
        pass


def _emit_miss(cache: str) -> None:
    try:
        from app.core.metrics import cache_miss_total
        cache_miss_total.labels(cache=cache).inc()
    except ImportError:
        pass


# ========== L1: LRU + TTL ==========

class _LruTtlStore:
    """进程内 LRU + TTL 存储（线程安全）"""

    def __init__(self, maxsize: int, lock: threading.Lock):
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()  # key → (value, expires_at)
        self._maxsize = maxsize
        self._lock = lock

    def get(self, key: str) -> Any:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return _MISS
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._data[key]
                return _MISS
            self._data.move_to_end(key)  # LRU: 命中即移到队尾
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)  # 淘汰最久未使用

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ========== 两级缓存 ==========

class MultiLevelCache:
    """L1（进程内 LRU+TTL）→ L2（Redis）两级读穿透缓存

    Args:
        namespace: 缓存命名空间（L2 键前缀 + 指标 label）
        ttl: 默认 TTL（秒），实际写入时加 ±10% 抖动
        maxsize: L1 容量上限
        use_l2: 是否启用 Redis L2（测试或纯本地场景可关）
        redis_factory: 自定义 Redis 客户端工厂（测试注入 fakeredis 用）
    """

    def __init__(
        self,
        namespace: str,
        ttl: float = 300.0,
        maxsize: int = 2000,
        use_l2: bool = True,
        redis_factory: Optional[Callable] = None,
    ):
        self.namespace = namespace
        self.ttl = ttl
        self._lock = threading.Lock()
        self._l1 = _LruTtlStore(maxsize, self._lock)
        self._use_l2 = use_l2
        self._redis_factory = redis_factory or _get_redis
        # singleflight: key → Event（进行中的加载）
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()

    # ---- 内部工具 ----

    def _l2_key(self, key: str) -> str:
        return f"cache:{self.namespace}:{key}"

    @staticmethod
    def _jitter(ttl: float) -> float:
        """TTL ±10% 随机抖动（雪崩防护）"""
        return ttl * random.uniform(0.9, 1.1)

    def _l2_get(self, key: str) -> Any:
        """从 L2 读取。返回 _MISS 表示未命中。"""
        if not self._use_l2:
            return _MISS
        try:
            client = self._redis_factory()
            if client is None:
                return _MISS
        except Exception:
            return _MISS
        try:
            raw = client.get(self._l2_key(key))
        except Exception as e:
            logger.debug(f"multi_cache[{self.namespace}]: L2 get failed: {e}")
            _mark_redis_down()
            return _MISS
        if raw is None:
            return _MISS
        try:
            wrapper = json.loads(raw)
            return wrapper.get("v")  # {"v": value}，None 值也能正确还原
        except (json.JSONDecodeError, AttributeError):
            return _MISS

    def _l2_set(self, key: str, value: Any, ttl: float) -> None:
        if not self._use_l2:
            return
        try:
            client = self._redis_factory()
            if client is None:
                return
        except Exception:
            return
        try:
            client.setex(self._l2_key(key), max(1, int(ttl)), json.dumps({"v": value}))
        except Exception as e:
            logger.debug(f"multi_cache[{self.namespace}]: L2 set failed (silent fallback): {e}")
            _mark_redis_down()

    def _l2_delete(self, key: str) -> None:
        if not self._use_l2:
            return
        try:
            client = self._redis_factory()
            if client is None:
                return
        except Exception:
            return
        try:
            client.delete(self._l2_key(key))
        except Exception:
            _mark_redis_down()

    # ---- 对外 API ----

    def get(self, key: str) -> Any:
        """逐级查找：L1 → L2（L2 命中回填 L1）。未命中返回 _MISS 哨兵。"""
        value = self._l1.get(key)
        if value is not _MISS:
            _emit_hit(self.namespace, "l1")
            return value

        value = self._l2_get(key)
        if value is not _MISS:
            _emit_hit(self.namespace, "l2")
            self._l1.set(key, value, self._jitter(self.ttl))  # 回填 L1
            return value

        _emit_miss(self.namespace)
        return _MISS

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """同时写入 L1 和 L2。None 值使用短 TTL（穿透防护）。"""
        effective_ttl = ttl if ttl is not None else self.ttl
        if value is None:
            effective_ttl = min(effective_ttl, _NULL_TTL)
        jittered = self._jitter(effective_ttl)
        self._l1.set(key, value, jittered)
        self._l2_set(key, value, jittered)

    def delete(self, key: str) -> None:
        """两级同时失效（写操作后调用）"""
        self._l1.delete(key)
        self._l2_delete(key)

    def clear_l1(self) -> None:
        """清空 L1（测试用）"""
        self._l1.clear()

    def get_or_load(self, key: str, loader: Callable[[], Any], ttl: Optional[float] = None) -> Any:
        """读穿透 + singleflight：缓存未命中时调用 loader 并写回

        并发 miss 时只有一个线程（leader）执行 loader，其余线程等待其完成后
        直接读缓存。leader 的 loader 抛异常时：异常传播给 leader，等待者
        自行调用 loader（不再等待，保证有界延迟）。

        Args:
            key: 缓存键（调用方负责哈希/规整）
            loader: 数据源加载函数（无参）
            ttl: 本次写入的 TTL（None 用默认）
        """
        value = self.get(key)
        if value is not _MISS:
            return value

        with self._inflight_lock:
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                is_leader = True
            else:
                is_leader = False

        if is_leader:
            try:
                # 双重检查：拿到 leader 身份前，上一个 leader 可能刚写完缓存
                value = self.get(key)
                if value is not _MISS:
                    return value
                value = loader()
                self.set(key, value, ttl)
                return value
            finally:
                with self._inflight_lock:
                    self._inflight.pop(key, None)
                event.set()

        # follower：等 leader 完成后读缓存；leader 失败则自己加载（不写回由自己决定）
        event.wait(timeout=90.0)
        value = self.get(key)
        if value is not _MISS:
            return value
        # leader 失败（或超时）：自己加载，成功则写回
        value = loader()
        self.set(key, value, ttl)
        return value


# ========== 模块级缓存实例（懒加载，读 Settings 配置） ==========

_instances_lock = threading.Lock()
_llm_cache: MultiLevelCache | None = None
_embed_cache: MultiLevelCache | None = None
_db_cache: MultiLevelCache | None = None


def get_llm_cache() -> MultiLevelCache:
    """LLM 结果缓存（相同 prompt 直接复用回答，省 token 省延迟）"""
    global _llm_cache
    if _llm_cache is None:
        with _instances_lock:
            if _llm_cache is None:
                settings = Settings()  # type: ignore[call-arg]
                _llm_cache = MultiLevelCache(
                    namespace="llm",
                    ttl=settings.CACHE_LLM_TTL,
                    maxsize=settings.CACHE_L1_MAXSIZE,
                )
    return _llm_cache


def get_embed_cache() -> MultiLevelCache:
    """Embedding 缓存（embedding 是确定性计算，可长 TTL）"""
    global _embed_cache
    if _embed_cache is None:
        with _instances_lock:
            if _embed_cache is None:
                settings = Settings()  # type: ignore[call-arg]
                _embed_cache = MultiLevelCache(
                    namespace="embed",
                    ttl=settings.CACHE_EMBED_TTL,
                    maxsize=settings.CACHE_L1_MAXSIZE,
                )
    return _embed_cache


def get_db_cache() -> MultiLevelCache:
    """DB 热点读缓存（订阅列表等，写操作后须 delete 失效）"""
    global _db_cache
    if _db_cache is None:
        with _instances_lock:
            if _db_cache is None:
                settings = Settings()  # type: ignore[call-arg]
                _db_cache = MultiLevelCache(
                    namespace="db",
                    ttl=settings.CACHE_DB_TTL,
                    maxsize=settings.CACHE_L1_MAXSIZE,
                )
    return _db_cache


def reset_caches_for_tests() -> None:
    """重建所有模块级缓存实例（仅测试用）"""
    global _llm_cache, _embed_cache, _db_cache
    with _instances_lock:
        _llm_cache = None
        _embed_cache = None
        _db_cache = None

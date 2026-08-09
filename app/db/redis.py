"""Redis 缓存客户端

提供 URL 防重检查和飞书 tenant_access_token 缓存功能。
"""
import redis


class RedisClient:
    """Redis 操作封装，管理 URL 去重和 Token 缓存"""

    # Redis Key 前缀
    URL_SET_KEY = "feishu_bot:processed_urls"
    TOKEN_KEY = "feishu_bot:tenant_access_token"

    def __init__(self, host: str = "127.0.0.1", port: int = 6379):
        """初始化 Redis 连接"""
        self._client = redis.Redis(host=host, port=port, decode_responses=True)

    # ========== URL 防重 ==========

    def is_url_processed(self, url: str) -> bool:
        """检查 URL 是否已经处理过（Redis Set 成员检查）"""
        return bool(self._client.sismember(self.URL_SET_KEY, url))

    def mark_url_processed(self, url: str) -> None:
        """标记 URL 为已处理（加入 Redis Set）"""
        self._client.sadd(self.URL_SET_KEY, url)

    # ========== Token 缓存 ==========

    def cache_token(self, token: str, ttl: int = 7200) -> None:
        """缓存飞书 tenant_access_token（默认 TTL 2 小时）"""
        self._client.setex(self.TOKEN_KEY, ttl, token)

    def get_cached_token(self) -> str | None:
        """获取缓存的飞书 token，不存在返回 None"""
        result = self._client.get(self.TOKEN_KEY)
        return result if result else None

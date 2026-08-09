"""Simple in-memory TTL cache for frequently-read chat metadata.

Used by the Feishu card action handler to avoid repeated DB queries
on every button click. Chat metadata (type, owner, preferences) rarely
changes, so a short TTL cache eliminates ~4 DB round-trips per click.

Thread-safe: uses threading.Lock for concurrent access from WS thread
and APScheduler thread pool.
"""

import time
import threading


class TTLCache:
    """Thread-safe in-memory cache with per-key TTL expiration."""

    def __init__(self, ttl: int = 300):
        self._data: dict[str, dict] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str):
        """Return cached value or None if expired/missing."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.time() - entry["ts"] < self._ttl:
                return entry["value"]
            del self._data[key]
            return None

    def set(self, key: str, value) -> None:
        """Store a value with current timestamp."""
        with self._lock:
            self._data[key] = {"value": value, "ts": time.time()}

    def delete(self, key: str) -> None:
        """Explicitly invalidate a key."""
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._data.clear()


# Global instances — one for chat metadata, one for preferences
# TTL=300s is short enough to pick up changes and long enough to
# cover bursts of card button clicks.
chat_meta_cache = TTLCache(ttl=300)   # key: "{chat_id}:type" / "{chat_id}:owner"
chat_pref_cache = TTLCache(ttl=300)   # key: "{chat_id}:pref"

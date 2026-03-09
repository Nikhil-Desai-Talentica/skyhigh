"""
Abuse and bot detection: rate-limit rapid seat-map access and temporarily block clients.
"""
from __future__ import annotations

import logging
import os
import re
import time
from threading import Lock
from typing import Optional

import redis

logger = logging.getLogger(__name__)

# Only allow safe characters for Redis keys (IPs, hashes)
_CLIENT_ID_PATTERN = re.compile(r"^[a-fA-F0-9.:\-]+$")

# Defaults: 15 seat-related requests per 60s triggers block; block 5 minutes
WINDOW_SECONDS = int(os.environ.get("ABUSE_WINDOW_SECONDS", "60"))
MAX_REQUESTS_PER_WINDOW = int(os.environ.get("ABUSE_MAX_REQUESTS", "15"))
BLOCK_DURATION_SECONDS = int(os.environ.get("ABUSE_BLOCK_DURATION_SECONDS", "300"))

REDIS_KEY_PREFIX = "abuse"
REDIS_COUNT_KEY = f"{REDIS_KEY_PREFIX}:count:{{client_id}}"
REDIS_BLOCK_KEY = f"{REDIS_KEY_PREFIX}:blocked:{{client_id}}"


def get_client_id(forwarded_for: Optional[str], real_ip: Optional[str], client_host: Optional[str]) -> str:
    """
    Derive a stable client identifier from request headers/socket.
    Prefer X-Forwarded-For (first IP) or X-Real-IP when behind a proxy.
    """
    if forwarded_for:
        # First IP in X-Forwarded-For is the client
        first = forwarded_for.strip().split(",")[0].strip()
        if first:
            return _sanitize_client_id(first)
    if real_ip:
        return _sanitize_client_id(real_ip.strip())
    if client_host:
        return _sanitize_client_id(client_host)
    return "unknown"


def _sanitize_client_id(raw: str) -> str:
    """Allow only characters safe for Redis keys and typical IPs."""
    s = raw.strip()
    if not s:
        return "unknown"
    if _CLIENT_ID_PATTERN.match(s):
        return s
    # Fallback: hash or truncate to safe chars
    return "".join(c if c.isalnum() or c in ".:-" else "_" for c in s[:64]) or "unknown"


class AbuseDetector:
    """
    Tracks seat-related request rate per client and temporarily blocks clients
    that exceed the threshold (e.g. rapid access to multiple seat maps).
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or os.environ.get("REDIS_URL")
        self._client: Optional[redis.Redis] = None
        self._in_memory: dict[str, list[float]] = {}
        self._in_memory_blocks: dict[str, float] = {}
        self._lock = Lock()

    def _get_redis(self) -> Optional[redis.Redis]:
        if self._redis_url is None:
            return None
        if self._client is None:
            try:
                self._client = redis.from_url(self._redis_url)
                self._client.ping()
            except Exception:  # noqa: BLE001
                logger.warning("Redis unavailable for abuse detection; using in-memory fallback")
                self._client = None
        return self._client

    def is_blocked(self, client_id: str) -> bool:
        """Return True if this client is currently blocked."""
        r = self._get_redis()
        if r is not None:
            try:
                return r.exists(REDIS_BLOCK_KEY.format(client_id=client_id)) > 0
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            until = self._in_memory_blocks.get(client_id)
            if until is not None and time.monotonic() < until:
                return True
            if until is not None:
                del self._in_memory_blocks[client_id]
            return False

    def record_seat_access(self, client_id: str) -> None:
        """
        Record one seat-related request. If the client exceeds the rate limit
        in the current window, they are marked blocked for BLOCK_DURATION_SECONDS.
        """
        r = self._get_redis()
        if r is not None:
            try:
                self._record_redis(r, client_id)
                return
            except Exception:  # noqa: BLE001
                pass
        self._record_in_memory(client_id)

    def _record_redis(self, r: redis.Redis, client_id: str) -> None:
        count_key = REDIS_COUNT_KEY.format(client_id=client_id)
        block_key = REDIS_BLOCK_KEY.format(client_id=client_id)
        pipe = r.pipeline()
        pipe.incr(count_key)
        pipe.expire(count_key, WINDOW_SECONDS)
        results = pipe.execute()
        count = results[0]
        if count >= MAX_REQUESTS_PER_WINDOW:
            r.setex(block_key, BLOCK_DURATION_SECONDS, "1")
            logger.warning(
                "Abuse detection: blocked client %s for %ss (count=%s in window)",
                client_id,
                BLOCK_DURATION_SECONDS,
                count,
            )

    def _record_in_memory(self, client_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            if self._in_memory_blocks.get(client_id, 0) > now:
                return
            times = self._in_memory.setdefault(client_id, [])
            cutoff = now - WINDOW_SECONDS
            times[:] = [t for t in times if t > cutoff]
            times.append(now)
            if len(times) >= MAX_REQUESTS_PER_WINDOW:
                self._in_memory_blocks[client_id] = now + BLOCK_DURATION_SECONDS
                logger.warning(
                    "Abuse detection: blocked client %s for %ss (in-memory)",
                    client_id,
                    BLOCK_DURATION_SECONDS,
                )

    def block_remaining_seconds(self, client_id: str) -> int:
        """Return how many seconds the client remains blocked (0 if not blocked)."""
        r = self._get_redis()
        if r is not None:
            try:
                ttl = r.ttl(REDIS_BLOCK_KEY.format(client_id=client_id))
                return max(0, int(ttl)) if ttl > 0 else 0
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            until = self._in_memory_blocks.get(client_id)
            if until is None:
                return 0
            remaining = until - time.monotonic()
            return max(0, int(remaining))

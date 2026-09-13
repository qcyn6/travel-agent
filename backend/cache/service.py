import json
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.cache.wsl import ensure_wsl_service_available


class JsonCache(Protocol):
    async def get_json(self, key: str): ...
    async def set_json(self, key: str, value, *, ttl_seconds: int) -> bool: ...


class NullCache:
    async def get_json(self, key: str):
        return None

    async def set_json(self, key: str, value, *, ttl_seconds: int) -> bool:
        return False


class RedisJsonCache:
    def __init__(
        self,
        url: str,
        *,
        key_prefix: str = "travel-agent",
        socket_timeout_seconds: float = 1.0,
        client=None,
    ):
        self.key_prefix = key_prefix.strip(":")
        self.client = client or Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout_seconds,
            socket_timeout=socket_timeout_seconds,
        )

    async def get_json(self, key: str):
        try:
            value = await self.client.get(self._key(key))
            return json.loads(value) if value is not None else None
        except (RedisError, ValueError, TypeError):
            return None

    async def set_json(self, key: str, value, *, ttl_seconds: int) -> bool:
        try:
            await self.client.setex(
                self._key(key),
                ttl_seconds,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
            return True
        except (RedisError, ValueError, TypeError):
            return False

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"


def build_cache(settings) -> JsonCache:
    if not settings.redis_url:
        return NullCache()
    ensure_wsl_service_available(
        settings.redis_wsl_distro,
        settings.redis_url,
        startup_timeout_seconds=settings.redis_wsl_startup_timeout_seconds,
    )
    return RedisJsonCache(
        settings.redis_url,
        key_prefix=settings.redis_key_prefix,
        socket_timeout_seconds=settings.redis_timeout_seconds,
    )

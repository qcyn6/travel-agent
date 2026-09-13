import httpx
import pytest
from redis.exceptions import ConnectionError

from backend.cache import RedisJsonCache
from backend.cache.wsl import ensure_wsl_service_available
from backend.poi import AmapAttractionClient


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.writes = []

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, *, ttl_seconds):
        self.values[key] = value
        self.writes.append((key, ttl_seconds))
        return True


@pytest.mark.asyncio
async def test_amap_poi_uses_cache_aside_and_expected_ttl():
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"status": "1", "pois": [
                {"id": "A1", "name": "灵隐寺", "cityname": "杭州市", "location": "120.1,30.2"}
            ]},
        )

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AmapAttractionClient(
            "test-key", client=client, cache=cache, cache_ttl_seconds=86400
        )
        first = await provider.search_attractions("杭州市", limit=10)
        second = await provider.search_attractions("杭州市", limit=10)

    assert calls == 1
    assert first == second
    assert cache.writes == [("poi:杭州市:10", 86400)]


class BrokenRedisClient:
    async def get(self, key):
        raise ConnectionError("offline")

    async def setex(self, key, ttl, value):
        raise ConnectionError("offline")


@pytest.mark.asyncio
async def test_redis_failure_is_non_blocking():
    cache = RedisJsonCache("redis://unused", client=BrokenRedisClient())

    assert await cache.get_json("weather:杭州市:2026-09-20") is None
    assert await cache.set_json("route:A:B:walking", {"duration": 10}, ttl_seconds=60) is False


def test_wsl_keepalive_starts_when_missing_and_waits_for_port(monkeypatch):
    calls = []

    class Completed:
        returncode = 1

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("backend.cache.wsl.os.name", "nt")
    monkeypatch.setattr("backend.cache.wsl.subprocess.run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(
        "backend.cache.wsl.subprocess.Popen",
        lambda args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        "backend.cache.wsl.socket.create_connection",
        lambda *args, **kwargs: Connection(),
    )

    available = ensure_wsl_service_available(
        "Ubuntu", "redis://127.0.0.1:6379/0", startup_timeout_seconds=0.5
    )

    assert available is True
    assert calls == [["wsl.exe", "-d", "Ubuntu", "--exec", "sleep", "infinity"]]

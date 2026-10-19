"""Shared, bounded request budgets. Production fails closed if Redis is down."""
from __future__ import annotations

import hashlib
import asyncio
import math
from threading import Lock
import time

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse


_HIT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""


class RequestLimiter:
    def __init__(self):
        self._clients = {}
        self._counts = {}
        self._lock = Lock()

    def hit(self, key: str, limit: int, seconds: int, settings) -> None:
        digest = hashlib.sha256(key.encode()).hexdigest()
        redis_key = f"{settings.redis_key_prefix}:limits:{digest}"
        count = ttl = None
        if settings.redis_url:
            try:
                with self._lock:
                    client = self._clients.get(settings.redis_url)
                    if client is None:
                        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
                        self._clients[settings.redis_url] = client
                count, ttl = client.eval(_HIT, 1, redis_key, seconds)
            except (RedisError, ValueError, TypeError):
                if settings.app_env == "production":
                    raise HTTPException(503, "请求保护服务暂不可用，请稍后再试", headers={"Retry-After": "5"}) from None
        elif settings.app_env == "production":
            raise HTTPException(503, "请求保护服务暂不可用", headers={"Retry-After": "5"})
        if count is None:
            now = time.monotonic()
            with self._lock:
                if len(self._counts) >= 10000:
                    self._counts = {k: v for k, v in self._counts.items() if v[1] > now}
                    if len(self._counts) >= 10000 and redis_key not in self._counts:
                        raise HTTPException(503, "请求保护服务繁忙，请稍后再试")
                count, expiry = self._counts.get(redis_key, (0, now + seconds))
                if expiry <= now:
                    count, expiry = 0, now + seconds
                count += 1
                self._counts[redis_key] = (count, expiry)
                ttl = max(1, math.ceil(expiry - now))
        if count > limit:
            raise HTTPException(429, "请求次数已达上限，请稍后再试", headers={"Retry-After": str(max(1, ttl))})

    def close(self):
        for client in self._clients.values():
            client.close()


class PublicProtectionMiddleware:
    """Pure ASGI: leave SSE iteration/cancellation semantics untouched."""
    def __init__(self, app, settings_provider):
        self.app, self.settings_provider = app, settings_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            await send(message)
        settings = self.settings_provider()
        if not getattr(request.app.state, "auth_bypass", False):
            try:
                length = request.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > 1024 * 1024):
                    raise HTTPException(413, "请求内容过大或长度不合法")
                if settings.rate_limit_enabled and request.method == "POST":
                    policy = {
                        "/api/v1/auth/login": (settings.login_requests_per_minute, 60),
                        "/api/v1/auth/register": (settings.register_requests_per_hour, 3600),
                    }.get(request.url.path)
                    if policy:
                        ip = request.client.host if request.client else "unknown"
                        await asyncio.to_thread(request.app.state.request_limiter.hit,
                            f"public:{request.url.path}:{ip}", *policy, settings)
                if request.method in {"POST", "PUT", "PATCH"}:
                    chunks, size = [], 0
                    try:
                        async with asyncio.timeout(10):
                            while True:
                                message = await receive()
                                if message["type"] == "http.disconnect":
                                    return
                                chunk = message.get("body", b"")
                                size += len(chunk)
                                if size > 1024 * 1024:
                                    raise HTTPException(413, "请求内容过大")
                                chunks.append(chunk)
                                if not message.get("more_body", False):
                                    break
                    except TimeoutError:
                        raise HTTPException(408, "请求上传超时") from None
                    original_receive, body = receive, b"".join(chunks)
                    delivered = False
                    async def replay_receive():
                        nonlocal delivered
                        if not delivered:
                            delivered = True
                            return {"type": "http.request", "body": body, "more_body": False}
                        return await original_receive()
                    receive = replay_receive
            except HTTPException as exc:
                return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                          headers=exc.headers)(scope, receive, protected_send)
        await self.app(scope, receive, protected_send)


def enforce_user_budget(request: Request, user_id: str, settings) -> None:
    if not settings.rate_limit_enabled or getattr(request.app.state, "auth_bypass", False):
        return
    limiter = request.app.state.request_limiter
    limiter.hit(f"user:{user_id}", settings.user_requests_per_minute, 60, settings)
    path = request.url.path
    compute = (
        request.method == "POST" and (
            path.endswith("/messages") or path.endswith("/resolve")
            or "/retrieval/" in path or path == "/api/v1/planning/city"
            or path == "/api/v1/travel/plan" or "/attraction-replacement" in path)
    ) or (request.method == "GET" and (
        path == "/api/v1/retrieval/vector"
        or path.endswith("/attractions") and request.query_params.get("use_amap", "true").lower() != "false"))
    if compute:
        limiter.hit("global-compute-minute", settings.global_compute_requests_per_minute, 60, settings)
        limiter.hit("global-compute-day", settings.global_compute_requests_per_day, 86400, settings)
        limiter.hit(f"compute-minute:{user_id}", settings.user_compute_requests_per_minute, 60, settings)
        limiter.hit(f"compute-day:{user_id}", settings.user_compute_requests_per_day, 86400, settings)


def reject_legacy_planning(request: Request):
    from backend.config import get_settings
    if get_settings().app_env == "production":
        raise HTTPException(410, "请通过规划对话确认需求后生成方案")

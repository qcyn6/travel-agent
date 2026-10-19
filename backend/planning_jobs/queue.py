import asyncio
from dataclasses import dataclass
from functools import lru_cache
import logging
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from backend.cache.wsl import ensure_wsl_service_available
from backend.observability import metrics


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanningQueueMessage:
    message_id: str
    job_id: str


class PlanningQueue(Protocol):
    enabled: bool

    async def enqueue(self, job_id: str, *, generation: int = 0) -> bool: ...

    async def receive(
        self,
        consumer_id: str,
        *,
        block_seconds: float,
        stale_after_seconds: float,
    ) -> PlanningQueueMessage | None: ...

    async def ack(self, message_id: str) -> bool: ...

    async def close(self) -> None: ...


class NullPlanningQueue:
    enabled = False

    async def enqueue(self, job_id: str, *, generation: int = 0) -> bool:
        del job_id, generation
        return False

    async def receive(
        self,
        consumer_id: str,
        *,
        block_seconds: float,
        stale_after_seconds: float,
    ) -> PlanningQueueMessage | None:
        del consumer_id, block_seconds, stale_after_seconds
        return None

    async def ack(self, message_id: str) -> bool:
        del message_id
        return False

    async def close(self) -> None:
        return None


class RedisPlanningQueue:
    """Redis Streams queue with idempotent enqueue and pending-message reclaim."""

    enabled = True

    def __init__(
        self,
        url: str,
        *,
        key_prefix: str = "travel-agent",
        socket_timeout_seconds: float = 6.0,
        dedup_ttl_seconds: int = 86400,
        max_length: int = 10000,
        client=None,
    ):
        self.key_prefix = key_prefix.strip(":")
        self.stream_key = f"{self.key_prefix}:planning:queue"
        self.group_name = f"{self.key_prefix}:planning:workers"
        self.dedup_ttl_seconds = max(60, int(dedup_ttl_seconds))
        self.max_length = max(100, int(max_length))
        self.client = client or Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout_seconds,
            socket_timeout=socket_timeout_seconds,
        )
        self._owns_client = client is None
        self._group_ready = False
        self._group_lock = asyncio.Lock()
        self._claim_cursor = "0-0"
        self._warning_emitted = False

    async def enqueue(self, job_id: str, *, generation: int = 0) -> bool:
        reserved = False
        dedup_key = f"{self.key_prefix}:planning:dispatch:{job_id}"
        # retry_count is retained when a checkpointed job resumes. Each
        # queued execution round needs its own dispatch reservation, while
        # reconnects within the same round must still be deduplicated.
        if generation:
            dedup_key += f":{generation}"
        try:
            await self._ensure_group()
            reserved = bool(
                await self.client.set(
                    dedup_key,
                    "1",
                    nx=True,
                    ex=self.dedup_ttl_seconds,
                )
            )
            if not reserved:
                return True
            await self.client.xadd(
                self.stream_key,
                {"job_id": job_id, "generation": str(generation)},
                maxlen=self.max_length,
                approximate=True,
            )
            metrics.inc(
                "travel_agent_planning_queue_enqueues_total",
                {"outcome": "accepted"},
            )
            return True
        except (RedisError, ValueError, TypeError) as exc:
            if reserved:
                try:
                    await self.client.delete(dedup_key)
                except RedisError:
                    pass
            self._warn_once(exc)
            metrics.inc(
                "travel_agent_planning_queue_errors_total",
                {"operation": "enqueue"},
            )
            return False

    async def receive(
        self,
        consumer_id: str,
        *,
        block_seconds: float,
        stale_after_seconds: float,
    ) -> PlanningQueueMessage | None:
        try:
            await self._ensure_group()
            claimed = await self.client.xautoclaim(
                self.stream_key,
                self.group_name,
                consumer_id,
                max(1, int(stale_after_seconds * 1000)),
                start_id=self._claim_cursor,
                count=1,
            )
            if claimed:
                self._claim_cursor = _decode(claimed[0])
                messages = claimed[1] if len(claimed) > 1 else []
                message = _message_from_entries(messages)
                if message is not None:
                    metrics.inc(
                        "travel_agent_planning_queue_receives_total",
                        {"source": "reclaimed"},
                    )
                    return message

            response = await self.client.xreadgroup(
                self.group_name,
                consumer_id,
                {self.stream_key: ">"},
                count=1,
                block=max(1, int(block_seconds * 1000)),
            )
            if not response:
                return None
            stream_entries = response[0][1] if len(response[0]) > 1 else []
            message = _message_from_entries(stream_entries)
            if message is not None:
                metrics.inc(
                    "travel_agent_planning_queue_receives_total",
                    {"source": "new"},
                )
            return message
        except (RedisError, ValueError, TypeError) as exc:
            self._warn_once(exc)
            metrics.inc(
                "travel_agent_planning_queue_errors_total",
                {"operation": "receive"},
            )
            return None

    async def ack(self, message_id: str) -> bool:
        try:
            acknowledged = await self.client.xack(
                self.stream_key,
                self.group_name,
                message_id,
            )
            return bool(acknowledged)
        except (RedisError, ValueError, TypeError) as exc:
            self._warn_once(exc)
            metrics.inc(
                "travel_agent_planning_queue_errors_total",
                {"operation": "ack"},
            )
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        async with self._group_lock:
            if self._group_ready:
                return
            try:
                await self.client.xgroup_create(
                    self.stream_key,
                    self.group_name,
                    id="0-0",
                    mkstream=True,
                )
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._group_ready = True

    def _warn_once(self, exc: Exception) -> None:
        if not self._warning_emitted:
            logger.warning(
                "规划任务 Redis 队列不可用，将回退到进程内 worker：%s",
                type(exc).__name__,
            )
            self._warning_emitted = True


def _message_from_entries(entries) -> PlanningQueueMessage | None:
    if not entries:
        return None
    raw_message_id, raw_fields = entries[0]
    if not isinstance(raw_fields, dict):
        return None
    raw_job_id = raw_fields.get("job_id", raw_fields.get(b"job_id"))
    if raw_job_id is None:
        return None
    job_id = _decode(raw_job_id)
    if not job_id:
        return None
    return PlanningQueueMessage(
        message_id=_decode(raw_message_id),
        job_id=job_id,
    )


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@lru_cache(maxsize=8)
def _cached_redis_queue(
    url: str,
    key_prefix: str,
    socket_timeout_seconds: float,
    dedup_ttl_seconds: int,
    max_length: int,
) -> RedisPlanningQueue:
    return RedisPlanningQueue(
        url,
        key_prefix=key_prefix,
        socket_timeout_seconds=socket_timeout_seconds,
        dedup_ttl_seconds=dedup_ttl_seconds,
        max_length=max_length,
    )


def build_planning_queue(settings) -> PlanningQueue:
    if not bool(getattr(settings, "planning_queue_enabled", False)):
        return NullPlanningQueue()
    url = getattr(settings, "redis_url", None)
    if not url:
        logger.warning("已启用规划任务队列但未配置 REDIS_URL，将使用进程内 worker")
        return NullPlanningQueue()
    ensure_wsl_service_available(
        getattr(settings, "redis_wsl_distro", None),
        url,
        startup_timeout_seconds=float(
            getattr(settings, "redis_wsl_startup_timeout_seconds", 5.0)
        ),
    )
    block_seconds = float(getattr(settings, "planning_queue_block_seconds", 5.0))
    socket_timeout = max(
        float(getattr(settings, "redis_timeout_seconds", 1.0)),
        block_seconds + 1.0,
    )
    return _cached_redis_queue(
        url,
        str(getattr(settings, "redis_key_prefix", "travel-agent")),
        socket_timeout,
        int(getattr(settings, "planning_queue_dedup_ttl_seconds", 86400)),
        int(getattr(settings, "planning_queue_max_length", 10000)),
    )

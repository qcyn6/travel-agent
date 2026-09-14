import pytest
from redis.exceptions import ConnectionError

from backend.observability.metrics import MetricsRegistry
from backend.planning_jobs.queue import (
    NullPlanningQueue,
    RedisPlanningQueue,
)


class FakeRedis:
    def __init__(self):
        self.groups = []
        self.values = {}
        self.xadd_calls = []
        self.xack_calls = []

    async def xgroup_create(self, stream, group, *, id, mkstream):
        self.groups.append((stream, group, id, mkstream))

    async def set(self, key, value, *, nx, ex):
        assert nx is True
        assert ex >= 60
        if key in self.values:
            return None
        self.values[key] = value
        return True

    async def xadd(self, stream, fields, *, maxlen, approximate):
        self.xadd_calls.append((stream, fields, maxlen, approximate))
        return "1-0"

    async def xautoclaim(
        self,
        stream,
        group,
        consumer,
        min_idle_time,
        *,
        start_id,
        count,
    ):
        assert min_idle_time >= 1
        assert start_id == "0-0"
        assert count == 1
        return ("0-0", [])

    async def xreadgroup(self, group, consumer, streams, *, count, block):
        assert count == 1
        assert block >= 1
        stream = next(iter(streams))
        return [(stream, [("1-0", {"job_id": "job-1"})])]

    async def xack(self, stream, group, message_id):
        self.xack_calls.append((stream, group, message_id))
        return 1

    async def delete(self, key):
        self.values.pop(key, None)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_redis_planning_queue_deduplicates_and_reclaims_messages():
    client = FakeRedis()
    queue = RedisPlanningQueue(
        "redis://unused",
        key_prefix="test",
        client=client,
    )

    assert await queue.enqueue("job-1") is True
    assert await queue.enqueue("job-1") is True
    assert len(client.xadd_calls) == 1

    message = await queue.receive(
        "worker-1",
        block_seconds=1,
        stale_after_seconds=30,
    )
    assert message is not None
    assert message.message_id == "1-0"
    assert message.job_id == "job-1"
    assert await queue.ack(message.message_id) is True
    assert client.xack_calls[-1][2] == "1-0"


@pytest.mark.asyncio
async def test_redis_planning_queue_returns_false_when_redis_is_unavailable():
    class BrokenRedis(FakeRedis):
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("offline")

    client = BrokenRedis()
    queue = RedisPlanningQueue("redis://unused", client=client)

    assert await queue.enqueue("job-1") is False
    assert client.values == {}


@pytest.mark.asyncio
async def test_null_planning_queue_is_safe_for_development():
    queue = NullPlanningQueue()

    assert queue.enabled is False
    assert await queue.enqueue("job-1") is False
    assert await queue.receive(
        "worker-1",
        block_seconds=1,
        stale_after_seconds=30,
    ) is None
    assert await queue.ack("1-0") is False


def test_metrics_registry_renders_prometheus_histogram_and_labels():
    registry = MetricsRegistry()
    registry.define("test_requests_total", "counter", "requests")
    registry.define("test_duration_seconds", "histogram", "duration", buckets=(1.0, 2.0))
    registry.inc("test_requests_total", {"route": "/health"})
    registry.observe("test_duration_seconds", 1.5, {"route": "/health"})

    output = registry.render()

    assert "# TYPE test_requests_total counter" in output
    assert 'test_requests_total{route="/health"} 1' in output
    assert 'test_duration_seconds_bucket{le="1",route="/health"} 0' in output
    assert 'test_duration_seconds_bucket{le="2",route="/health"} 1' in output
    assert 'test_duration_seconds_bucket{le="+Inf",route="/health"} 1' in output

    registry.observe("test_duration_seconds", 1.0, {"route": "/health"})
    boundary_output = registry.render()
    assert 'test_duration_seconds_bucket{le="1",route="/health"} 1' in boundary_output
    assert 'test_duration_seconds_bucket{le="2",route="/health"} 2' in boundary_output

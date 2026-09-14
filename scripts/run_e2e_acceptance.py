"""Run the full opt-in acceptance flow for durable planning jobs.

The script exercises two already-running backend processes that share one
database. It creates one disposable conversation, checks duplicate submission
and SSE cursor recovery, optionally kills the owner process to verify lease
takeover, and confirms that only one job and one saved plan remain in MySQL.

The external service configuration is intentionally inherited from the
started backend processes. The script does not change project data other than
the disposable conversation it creates and removes at the end.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import get_engine  # noqa: E402
from backend.models.retrieval import AttractionDocument  # noqa: E402
from backend.models.travel_plan import TravelPlanResponse  # noqa: E402
from backend.rag.embedding import EmbeddingError  # noqa: E402
from backend.rag.hybrid import HybridRetrievalService  # noqa: E402


Event = tuple[int | None, str, dict[str, Any]]


@dataclass
class StreamResult:
    name: str
    status_code: int | None = None
    job_id: str | None = None
    events: list[Event] = field(default_factory=list)
    error: str | None = None


async def consume_stream(
    result: StreamResult,
    method: str,
    url: str,
    *,
    client: httpx.AsyncClient,
    ready: asyncio.Queue[StreamResult],
    stop_after_events: int | None = None,
) -> None:
    ready_sent = False
    try:
        async with client.stream(
            method,
            url,
            headers={"Accept": "text/event-stream"},
        ) as response:
            result.status_code = response.status_code
            result.job_id = response.headers.get("X-Planning-Job-Id")
            await ready.put(result)
            ready_sent = True
            response.raise_for_status()

            event_name = "message"
            event_id: int | None = None
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line == "":
                    if data_lines:
                        try:
                            payload = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(
                                f"{result.name} 收到无法解析的 SSE data"
                            ) from exc
                        result.events.append((event_id, event_name, payload))
                        if (
                            stop_after_events is not None
                            and len(result.events) >= stop_after_events
                        ):
                            return
                    event_name = "message"
                    event_id = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("id:"):
                    try:
                        event_id = int(line[3:].strip())
                    except ValueError:
                        event_id = None
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        if not ready_sent:
            await ready.put(result)


async def wait_for_ready(
    ready: asyncio.Queue[StreamResult],
    expected_name: str,
    *,
    timeout_seconds: float,
) -> StreamResult:
    result = await asyncio.wait_for(ready.get(), timeout=timeout_seconds)
    if result.name != expected_name:
        raise RuntimeError(f"收到错误的流启动通知：{result.name}，预期 {expected_name}")
    if result.status_code != 200 or not result.job_id:
        raise RuntimeError(
            f"{expected_name} 流启动失败：status={result.status_code}, "
            f"job={result.job_id}, error={result.error}"
        )
    return result


async def wait_for_event(
    result: StreamResult,
    event_name: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if any(item[1] == event_name for item in result.events):
            return
        if result.error:
            raise RuntimeError(f"{result.name} 未收到 {event_name}：{result.error}")
        await asyncio.sleep(0.1)
    raise RuntimeError(
        f"{result.name} 的 {event_name} 事件未在 {timeout_seconds:g} 秒内到达"
    )


def assert_monotonic_ids(result: StreamResult) -> None:
    ids = [event_id for event_id, _, _ in result.events if event_id is not None]
    if ids != sorted(set(ids)):
        raise RuntimeError(f"{result.name} 的 SSE 事件游标不递增：{ids}")
    if any(event_id < 1 for event_id in ids):
        raise RuntimeError(f"{result.name} 的 SSE 事件游标包含非法值：{ids}")


def stop_process(process_id: int) -> None:
    if process_id <= 0:
        raise ValueError(f"owner PID 必须为正数：{process_id}")
    os.kill(process_id, signal.SIGTERM)


async def prepare_conversation(
    client: httpx.AsyncClient,
    base_url: str,
) -> str:
    response = await client.post(f"{base_url}/api/v1/conversations")
    response.raise_for_status()
    conversation_id = response.json()["id"]
    draft = {
        "province": "浙江省",
        "cities": ["杭州市"],
        "days": 1,
        "interests": ["自然"],
        "preference_text": "行程轻松",
        "travel_scope": "single_city",
        "start_date": None,
        "transport_mode": "walking",
    }
    response = await client.patch(
        f"{base_url}/api/v1/conversations/{conversation_id}/requirement",
        json=draft,
    )
    response.raise_for_status()
    response = await client.post(
        f"{base_url}/api/v1/conversations/{conversation_id}/requirement/confirm"
    )
    response.raise_for_status()
    return conversation_id


def read_database_state(conversation_id: str) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as connection:
        job_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM planning_jobs "
                "WHERE conversation_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        ).scalar_one()
        job = connection.execute(
            text(
                "SELECT id, status, retry_count FROM planning_jobs "
                "WHERE conversation_id = :conversation_id "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"conversation_id": conversation_id},
        ).mappings().one_or_none()
        events = connection.execute(
            text(
                "SELECT pe.sequence_number, pe.event_type "
                "FROM planning_events pe "
                "JOIN planning_jobs pj ON pj.id = pe.job_id "
                "WHERE pj.conversation_id = :conversation_id "
                "ORDER BY pe.sequence_number"
            ),
            {"conversation_id": conversation_id},
        ).mappings().all()
        plan_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM travel_plans "
                "WHERE session_id = :conversation_id"
            ),
            {"conversation_id": conversation_id},
        ).scalar_one()
    return {
        "job_count": int(job_count),
        "job": dict(job) if job is not None else None,
        "events": [dict(event) for event in events],
        "plan_count": int(plan_count),
    }


def validate_database_state(
    state: dict[str, Any],
    *,
    expected_retry_count: int,
) -> None:
    if state["job_count"] != 1:
        raise RuntimeError(f"重复提交创建了多个 planning job：{state}")
    job = state["job"]
    if job is None or job["status"] != "completed":
        raise RuntimeError(f"任务最终状态异常：{state}")
    if int(job["retry_count"]) != expected_retry_count:
        raise RuntimeError(
            f"任务 retry_count 异常：实际={job['retry_count']}，"
            f"预期={expected_retry_count}"
        )

    event_ids = [int(event["sequence_number"]) for event in state["events"]]
    if event_ids != list(range(1, len(event_ids) + 1)):
        raise RuntimeError(f"数据库事件序列不连续：{event_ids}")
    event_counts: dict[str, int] = {}
    for event in state["events"]:
        event_counts[event["event_type"]] = event_counts.get(event["event_type"], 0) + 1
    if event_counts.get("plan_completed") != 1:
        raise RuntimeError(f"数据库中的 plan_completed 数量异常：{event_counts}")
    if state["plan_count"] != 1:
        raise RuntimeError(f"同一会话保存了多份最终方案：{state['plan_count']}")


async def validate_saved_plan(
    client: httpx.AsyncClient,
    base_url: str,
    conversation_id: str,
) -> None:
    response = await client.get(
        f"{base_url}/api/v1/conversations/{conversation_id}/plans"
    )
    response.raise_for_status()
    plans = response.json()
    if len(plans) != 1 or plans[0]["version"] != 1:
        raise RuntimeError(f"API 返回的方案版本异常：{plans}")
    saved = plans[0]
    result = TravelPlanResponse.model_validate(
        {"requirement": saved["requirement"], "plan": saved["plan"]}
    )
    if not result.plan.itinerary or result.plan.evaluation is None:
        raise RuntimeError("API 返回的已保存方案缺少 itinerary 或 evaluation")


async def verify_embedding_fallback() -> None:
    """Inject a vector failure and verify the deterministic BM25 fallback."""

    documents = [
        AttractionDocument(
            poi_id="acceptance-old-town",
            name="古城遗址",
            city="杭州市",
            category="风景名胜",
            description="历史文化遗址，适合慢慢散步和摄影。",
            source_url="https://example.gov.cn/old-town",
        ),
        AttractionDocument(
            poi_id="acceptance-wetland",
            name="湿地公园",
            city="杭州市",
            category="公园",
            description="自然生态丰富，适合休闲观鸟。",
            source_url="https://example.gov.cn/wetland",
        ),
    ]

    class Repository:
        def list_documents(self, city: str) -> list[AttractionDocument]:
            if city != "杭州市":
                raise AssertionError(f"unexpected city: {city}")
            return documents

    class FailingVectorService:
        async def index_city(self, city: str) -> int:
            raise EmbeddingError("controlled acceptance failure")

        async def search(self, **_: Any):
            raise AssertionError("vector search should not run after index failure")

    class StableReranker:
        async def rerank(self, **kwargs: Any) -> list[str]:
            return [item.poi_id for item in kwargs["candidates"]]

    results = await HybridRetrievalService(
        repository=Repository(),
        vector_service=FailingVectorService(),
        reranker=StableReranker(),
    ).search(
        city="杭州市",
        interests=["摄影"],
        preference_text="轻松散步",
        top_k=2,
    )
    if not results or any(item.vector_rank is not None for item in results):
        raise RuntimeError(f"Embedding 故障后未正确降级到 BM25：{results}")
    if not any(item.bm25_rank is not None for item in results):
        raise RuntimeError("Embedding 故障后没有可用的 BM25 结果")
    print("通过：Embedding 故障自动降级到 BM25")


async def delete_disposable_conversation(
    client: httpx.AsyncClient,
    conversation_id: str,
    base_urls: list[str],
) -> None:
    for base_url in dict.fromkeys(base_urls):
        try:
            response = await client.delete(
                f"{base_url}/api/v1/conversations/{conversation_id}"
            )
        except httpx.HTTPError:
            continue
        if response.status_code in {204, 404}:
            print(f"已清理临时会话：{conversation_id}")
            return
    print(f"警告：临时会话清理失败，请手动检查：{conversation_id}")


async def run(args: argparse.Namespace) -> None:
    await verify_embedding_fallback()
    timeout = httpx.Timeout(
        connect=args.request_timeout,
        read=None,
        write=args.request_timeout,
        pool=args.request_timeout,
    )
    client = httpx.AsyncClient(timeout=timeout)
    conversation_id: str | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        for base_url in (args.base_url_a, args.base_url_b):
            response = await client.get(f"{base_url}/health")
            response.raise_for_status()
        print("两个后端进程健康检查通过")

        conversation_id = await prepare_conversation(client, args.base_url_a)
        print(f"已创建临时会话：{conversation_id}")

        ready: asyncio.Queue[StreamResult] = asyncio.Queue()
        plan_path = f"/api/v1/conversations/{conversation_id}/plan"
        owner = StreamResult("owner-A")
        owner_task = asyncio.create_task(
            consume_stream(
                owner,
                "POST",
                f"{args.base_url_a}{plan_path}",
                client=client,
                ready=ready,
            )
        )
        tasks.append(owner_task)
        owner_ready = await wait_for_ready(
            ready,
            "owner-A",
            timeout_seconds=args.ready_timeout,
        )

        duplicate = StreamResult("duplicate-B")
        duplicate_task = asyncio.create_task(
            consume_stream(
                duplicate,
                "POST",
                f"{args.base_url_b}{plan_path}?after=0",
                client=client,
                ready=ready,
            )
        )
        tasks.append(duplicate_task)
        duplicate_ready = await wait_for_ready(
            ready,
            "duplicate-B",
            timeout_seconds=args.ready_timeout,
        )
        if duplicate_ready.job_id != owner_ready.job_id:
            raise RuntimeError(
                "重复 POST 没有复用同一个任务："
                f"A={owner_ready.job_id}, B={duplicate_ready.job_id}"
            )
        print(f"通过：重复提交复用同一 planning job：{owner_ready.job_id}")

        await wait_for_event(owner, "job_started", timeout_seconds=args.ready_timeout)

        probe = StreamResult("disconnect-probe-B")
        probe_task = asyncio.create_task(
            consume_stream(
                probe,
                "GET",
                f"{args.base_url_b}/api/v1/conversations/{conversation_id}/"
                "plan/events?after=0&job_id="
                f"{owner_ready.job_id}",
                client=client,
                ready=ready,
                stop_after_events=1,
            )
        )
        tasks.append(probe_task)
        await wait_for_ready(
            ready,
            "disconnect-probe-B",
            timeout_seconds=args.ready_timeout,
        )
        await asyncio.wait_for(probe_task, timeout=args.ready_timeout)
        if probe.error:
            raise RuntimeError(f"断线探针失败：{probe.error}")
        if len(probe.events) != 1 or probe.events[0][0] is None:
            raise RuntimeError(f"断线探针没有拿到有效 SSE 游标：{probe.events}")
        cursor = int(probe.events[0][0])

        resume = StreamResult("resume-B")
        resume_task = asyncio.create_task(
            consume_stream(
                resume,
                "GET",
                f"{args.base_url_b}/api/v1/conversations/{conversation_id}/"
                f"plan/events?after={cursor}&job_id={owner_ready.job_id}",
                client=client,
                ready=ready,
            )
        )
        tasks.append(resume_task)
        await wait_for_ready(
            ready,
            "resume-B",
            timeout_seconds=args.ready_timeout,
        )
        if resume.job_id != owner_ready.job_id:
            raise RuntimeError("断线恢复返回了不同的 planning job")
        print(f"通过：SSE 断线后使用 after={cursor} 续传")

        if args.kill_pid is not None:
            await asyncio.sleep(max(0.0, args.kill_after))
            stop_process(args.kill_pid)
            print(f"已结束 owner 进程：{args.kill_pid}，等待备用进程接管")

        await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=args.timeout,
        )

        for stream in (owner, duplicate, resume):
            assert_monotonic_ids(stream)
        resume_ids = [event_id for event_id, _, _ in resume.events if event_id is not None]
        if not resume_ids or resume_ids[0] <= cursor:
            raise RuntimeError(
                f"断线恢复没有从新游标继续：cursor={cursor}, events={resume.events}"
            )
        for stream in (duplicate, resume):
            completed = [event for event in stream.events if event[1] == "plan_completed"]
            if len(completed) != 1:
                raise RuntimeError(
                    f"{stream.name} 的 plan_completed 数量异常：{stream.events}"
                )
            TravelPlanResponse.model_validate(completed[0][2])
        if args.kill_pid is None:
            owner_completed = [event for event in owner.events if event[1] == "plan_completed"]
            if len(owner_completed) != 1:
                raise RuntimeError("未故障模式下 owner 流没有完整收到 plan_completed")
        elif owner.error:
            print(f"owner-A 断开信息（预期）：{owner.error}")

        state = read_database_state(conversation_id)
        validate_database_state(
            state,
            expected_retry_count=2 if args.kill_pid is not None else 1,
        )
        await validate_saved_plan(client, args.base_url_b, conversation_id)
        print(
            "全链路 E2E 验收通过："
            f"job={owner_ready.job_id} "
            f"retry_count={state['job']['retry_count']} "
            f"events={len(state['events'])} plans={state['plan_count']}"
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if conversation_id is not None:
            await delete_disposable_conversation(
                client,
                conversation_id,
                [args.base_url_b, args.base_url_a],
            )
        await client.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实双进程旅行规划全链路 E2E 验收")
    parser.add_argument("--base-url-a", default="http://127.0.0.1:8000")
    parser.add_argument("--base-url-b", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--kill-pid",
        type=int,
        help="可选：结束 owner-A 所在进程，验证备用 worker 的租约接管",
    )
    parser.add_argument("--kill-after", type=float, default=1.0)
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="等待规划与接管完成的最长秒数",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=30.0,
        help="等待 SSE 连接或关键启动事件的最长秒数",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30.0,
        help="普通 HTTP 请求的连接/写入超时；SSE 读取不设超时",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        raise SystemExit(f"全链路 E2E 验收失败：{exc}") from exc

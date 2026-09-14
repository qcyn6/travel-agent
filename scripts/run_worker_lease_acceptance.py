"""Run a real two-process planning lease smoke test.

The script intentionally is opt-in because it calls the configured external
services and writes one disposable conversation to the configured database.
Start two backend processes first, using the same database and short lease
settings for a fast failure-recovery check.
"""

import argparse
import asyncio
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

from backend.database import get_engine


async def consume_stream(
    name: str,
    method: str,
    url: str,
    *,
    client: httpx.AsyncClient,
    ready: asyncio.Queue[tuple[str, int, str | None]],
    result: dict[str, Any],
) -> None:
    result["events"] = []
    try:
        async with client.stream(
            method,
            url,
            headers={"Accept": "text/event-stream"},
            timeout=None,
        ) as response:
            job_id = response.headers.get("X-Planning-Job-Id")
            await ready.put((name, response.status_code, job_id))
            response.raise_for_status()

            event_name = "message"
            event_id: int | None = None
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if not line:
                    if data_lines:
                        result["events"].append(
                            (
                                event_id,
                                event_name,
                                json.loads("\n".join(data_lines)),
                            )
                        )
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
    except Exception as exc:  # The owner is expected to lose its connection in failover mode.
        result["error"] = f"{type(exc).__name__}: {exc}"


async def wait_for_event(
    result: dict[str, Any],
    event_name: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if any(item[1] == event_name for item in result.get("events", [])):
            return
        await asyncio.sleep(0.1)
    raise RuntimeError(f"流 {event_name} 事件未在 {timeout_seconds:g} 秒内到达")


def stop_process(process_id: int) -> None:
    if os.name == "nt":
        os.kill(process_id, signal.SIGTERM)
    else:
        os.kill(process_id, signal.SIGTERM)


async def run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        create_response = await client.post(f"{args.base_url_a}/api/v1/conversations")
        create_response.raise_for_status()
        conversation_id = create_response.json()["id"]
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
        patch_response = await client.patch(
            f"{args.base_url_a}/api/v1/conversations/{conversation_id}/requirement",
            json=draft,
        )
        patch_response.raise_for_status()
        confirm_response = await client.post(
            f"{args.base_url_a}/api/v1/conversations/{conversation_id}/requirement/confirm"
        )
        confirm_response.raise_for_status()

        ready: asyncio.Queue[tuple[str, int, str | None]] = asyncio.Queue()
        stream_a: dict[str, Any] = {}
        stream_b: dict[str, Any] = {}
        plan_url = f"/api/v1/conversations/{conversation_id}/plan"
        task_a = asyncio.create_task(
            consume_stream(
                "A",
                "POST",
                f"{args.base_url_a}{plan_url}",
                client=client,
                ready=ready,
                result=stream_a,
            )
        )
        name_a, status_a, job_a = await asyncio.wait_for(ready.get(), timeout=20)
        if name_a != "A" or status_a != 200 or not job_a:
            raise RuntimeError(f"owner 流启动失败：status={status_a}, job={job_a}")

        task_b = asyncio.create_task(
            consume_stream(
                "B",
                "GET",
                f"{args.base_url_b}/api/v1/conversations/{conversation_id}/plan/events",
                client=client,
                ready=ready,
                result=stream_b,
            )
        )
        name_b, status_b, job_b = await asyncio.wait_for(ready.get(), timeout=20)
        if name_b != "B" or status_b != 200 or job_b != job_a:
            raise RuntimeError(f"观察流启动失败：status={status_b}, job={job_b}")

        await wait_for_event(stream_a, "job_started", timeout_seconds=20)
        if args.kill_pid is not None:
            await asyncio.sleep(args.kill_after)
            stop_process(args.kill_pid)
            print(f"已结束 owner 进程：{args.kill_pid}")

        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=args.timeout)

    if not any(item[1] == "plan_completed" for item in stream_b.get("events", [])):
        raise RuntimeError("接管后的观察流没有收到 plan_completed")

    for name, stream in (("A", stream_a), ("B", stream_b)):
        ids = [item[0] for item in stream.get("events", []) if item[0] is not None]
        if ids != sorted(set(ids)):
            raise RuntimeError(f"流 {name} 的事件游标不递增：{ids}")
        if stream.get("error"):
            print(f"流 {name} 结束信息：{stream['error']}")

    engine = get_engine()
    with engine.connect() as connection:
        job = connection.execute(
            text(
                "SELECT status, retry_count FROM planning_jobs "
                "WHERE conversation_id = :conversation_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"conversation_id": conversation_id},
        ).mappings().one()
        event_counts = connection.execute(
            text(
                "SELECT event_type, COUNT(*) AS count FROM planning_events pe "
                "JOIN planning_jobs pj ON pj.id = pe.job_id "
                "WHERE pj.conversation_id = :conversation_id GROUP BY event_type"
            ),
            {"conversation_id": conversation_id},
        ).mappings().all()
    counts = {row["event_type"]: int(row["count"]) for row in event_counts}
    expected_retries = 2 if args.kill_pid is not None else 1
    if job["status"] != "completed":
        raise RuntimeError(f"任务最终状态不是 completed：{job['status']}")
    if int(job["retry_count"]) != expected_retries:
        raise RuntimeError(
            f"任务接管次数不符合预期：retry_count={job['retry_count']}, "
            f"expected={expected_retries}"
        )
    if counts.get("plan_completed") != 1:
        raise RuntimeError(f"数据库中的 plan_completed 数量异常：{counts}")

    print(
        "租约验收通过："
        f"conversation={conversation_id} job={job_a} "
        f"status={job['status']} retry_count={job['retry_count']} "
        f"plan_completed={counts.get('plan_completed', 0)}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实双进程规划 worker lease 验收")
    parser.add_argument("--base-url-a", default="http://127.0.0.1:8000")
    parser.add_argument("--base-url-b", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--kill-pid",
        type=int,
        help="可选：任务已开始后结束 owner 进程，验证租约接管",
    )
    parser.add_argument("--kill-after", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))

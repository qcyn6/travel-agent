"""Run the durable Redis Streams planning worker.

Start this process separately from one or more FastAPI processes after
setting PLANNING_QUEUE_ENABLED=true and configuring REDIS_URL. The worker
acknowledges a queue message only after the durable planning job has reached a
terminal state. A process crash leaves the message pending so another worker
can reclaim it after the lease timeout.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import socket
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.application import PlanningApplicationService  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.observability import build_alert_sink, metrics  # noqa: E402
from backend.planning_jobs import PlanningJobRepository  # noqa: E402
from backend.planning_jobs.queue import build_planning_queue  # noqa: E402


logger = logging.getLogger("travel_agent.planning_worker")


async def enqueue_queued_jobs(queue, *, limit: int) -> int:
    with open_session() as session:
        jobs = PlanningJobRepository(session).list_queued(limit=limit)
        job_ids = [job.id for job in jobs]
    queued = 0
    for job_id in job_ids:
        if await queue.enqueue(job_id):
            queued += 1
    return queued


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    queue = build_planning_queue(settings)
    if not getattr(queue, "enabled", False):
        raise RuntimeError(
            "规划队列未启用，请设置 PLANNING_QUEUE_ENABLED=true 并配置 REDIS_URL"
        )

    alert_sink = build_alert_sink(settings)
    service = PlanningApplicationService(
        settings,
        queue=queue,
        alert_sink=alert_sink,
    )
    consumer_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            try:
                loop.add_signal_handler(signal_value, request_stop)
            except (NotImplementedError, RuntimeError):
                pass

    last_reconcile = 0.0
    logger.info("planning worker started: consumer=%s", consumer_id)
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - last_reconcile >= args.reconcile_seconds:
                queued = await enqueue_queued_jobs(
                    queue,
                    limit=args.reconcile_limit,
                )
                if queued:
                    logger.info("queued orphan planning jobs: count=%s", queued)
                last_reconcile = now

            message = await queue.receive(
                consumer_id,
                block_seconds=args.block_seconds,
                stale_after_seconds=float(settings.planning_worker_stale_seconds),
            )
            if message is None:
                continue

            metrics.inc("travel_agent_planning_jobs_total", {"status": "dequeued"})
            try:
                await service.run_queued_job(message.job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                metrics.inc(
                    "travel_agent_planning_jobs_total",
                    {"status": "worker_error"},
                )
                logger.exception(
                    "queued planning job crashed and will be reclaimed: job_id=%s",
                    message.job_id,
                )
                try:
                    await alert_sink.notify(
                        event="planning_worker_crashed",
                        severity="critical",
                        message="规划 worker 执行任务时发生未处理异常",
                        metadata={
                            "job_id": message.job_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                except Exception:
                    logger.exception(
                        "worker crash alert delivery failed: job_id=%s",
                        message.job_id,
                    )
                continue

            if not await queue.ack(message.message_id):
                metrics.inc(
                    "travel_agent_planning_queue_errors_total",
                    {"operation": "ack"},
                )
                try:
                    await alert_sink.notify(
                        event="planning_queue_ack_failed",
                        severity="critical",
                        message="规划任务完成后队列确认失败，消息将等待重新领取",
                        metadata={"job_id": message.job_id},
                    )
                except Exception:
                    logger.exception(
                        "queue ack alert delivery failed: job_id=%s",
                        message.job_id,
                    )
    finally:
        await queue.close()
        close_alert = getattr(alert_sink, "close", None)
        if close_alert is not None:
            result = close_alert()
            if asyncio.iscoroutine(result):
                await result
        logger.info("planning worker stopped: consumer=%s", consumer_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redis Streams 规划任务 worker")
    parser.add_argument(
        "--block-seconds",
        type=float,
        default=5.0,
        help="没有新消息时阻塞等待的秒数",
    )
    parser.add_argument(
        "--reconcile-seconds",
        type=float,
        default=10.0,
        help="扫描并补发未入队 queued 任务的间隔",
    )
    parser.add_argument(
        "--reconcile-limit",
        type=int,
        default=100,
        help="每次最多补发的 queued 任务数",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        raise SystemExit(f"planning worker 启动失败：{exc}") from exc

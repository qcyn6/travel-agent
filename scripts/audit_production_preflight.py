"""Run production-mode API/worker against an existing isolated restore DB.

Creates a dedicated CRUD-only database account. Never changes the source DB,
never terminates an existing process, and keeps credentials in a private file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from audit_planning_robustness import Audit
from backend.config import get_settings
from backend.database import get_engine
from scripts.private_files import protect_private


def configure(path):
    for key, value in json.loads(path.read_text(encoding="utf-8")).items():
        setattr(get_settings(), key, value)


async def preflight(database, output):
    if not re.fullmatch(r"travel_restore_[A-Za-z0-9_]{8,48}", database):
        raise ValueError("Only an isolated restore database is allowed")
    settings = get_settings()
    url = make_url(settings.database_url)
    if database == url.database:
        raise ValueError("Refusing source database")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18083))
    suffix = uuid4().hex[:10]
    username, password = "travel_verify_" + suffix, secrets.token_hex(32)
    # These are parameter-bound account values, not SQL constructed from input.
    with get_engine().begin() as connection:
        exists = connection.scalar(text("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:db"), {"db": database})
        if not exists:
            raise ValueError("Restore database does not exist")
        connection.execute(text("CREATE USER :name@'localhost' IDENTIFIED BY :password"), {"name": username, "password": password})
        connection.execute(text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{database}`.* TO :name@'localhost'"), {"name": username})
    private = ROOT / ".tmp" / ("production-preflight-" + suffix)
    private.mkdir(parents=True)
    protect_private(private)
    changes = {
        "app_env": "production", "auth_required": True, "auth_secret": secrets.token_hex(32),
        "database_url": url.set(username=username, password=password, database=database).render_as_string(hide_password=False),
        "database_auto_initialize": False, "planning_queue_enabled": True,
        "cors_origins": ["https://preflight.invalid"], "redis_key_prefix": "qa-prod-" + suffix,
        "login_requests_per_minute": 2, "user_compute_requests_per_day": 2,
        "planning_max_active_jobs": 2, "planning_max_active_jobs_per_user": 1,
        "planning_worker_stale_seconds": 6, "planning_heartbeat_seconds": 1,
        "planning_checkpoint_path": str(private / "checkpoints.sqlite3"),
        "chroma_path": str(private / "chroma"), "bootstrap_admin_username": None,
        "bootstrap_admin_password": None,
    }
    config = private / "private-config.json"
    with os.fdopen(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(changes, handle)
    audit = Audit("http://127.0.0.1:18083", output)
    audit.results.update(isolated_database=database, dedicated_database_account=username,
                         only_crud_grants=True, credentials_file_not_published=True)
    children = []
    def start(mode):
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), mode, "--config", str(config)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        children.append(process)
        return process
    try:
        start("serve")
        async with httpx.AsyncClient(base_url=audit.base, timeout=120, trust_env=False) as client:
            deadline = time.monotonic() + 25
            while True:
                try:
                    response = await client.get("/ready", timeout=2)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Production API failed readiness")
                await asyncio.sleep(.2)
            audit.record("生产模式启动与依赖就绪", True)
            owner, _ = await audit.account(client, "prod")
            stranger, _ = await audit.account(client, "other")
            for path in ("/docs", "/openapi.json", "/redoc"):
                response = await client.get(path)
                audit.record("生产文档隐藏 " + path, response.status_code == 404, status=response.status_code)
            for path in ("/api/v1/conversations", "/api/v1/cities/杭州/attractions?use_amap=false",
                         "/api/v1/retrieval/vector?city=杭州&query=自然", "/metrics"):
                response = await client.get(path)
                audit.record("生产匿名保护 " + path, response.status_code == 401, status=response.status_code)
            response = await client.post("/api/v1/requirements/resolve", json={"text": "杭州一天"})
            audit.record("生产需求解析拒绝匿名", response.status_code == 401, status=response.status_code)
            response = await client.post("/api/v1/travel/plan", headers=owner, json={"text": "杭州一天"})
            audit.record("旧一键规划入口停用", response.status_code == 410, status=response.status_code)
            response = await client.get("/metrics", headers=owner)
            audit.record("生产指标仅限管理员", response.status_code == 403, status=response.status_code)
            general = await client.post("/api/v1/general-conversations", headers=owner)
            general.raise_for_status()
            status, _, events = await audit.stream(client, owner, "POST",
                f"/api/v1/general-conversations/{general.json()['id']}/messages", body={"message": "请简短介绍杭州西湖"})
            audit.record("加固后普通对话真实流式回复", status == 200 and any(e["event"] == "message_delta" for e in events)
                         and any(e["event"] == "message_done" and e["data"].get("status") == "completed" for e in events))
            _, _, dialogue_ok = await audit.live_dialogue(client, owner)
            response = await client.post("/api/v1/requirements/resolve", headers=owner, json={"text": "杭州一天"})
            audit.record("真实Redis用户计算次数限额", response.status_code == 429 and "Retry-After" in response.headers,
                         status=response.status_code)
            first = await audit.new(client, owner)
            second = await audit.new(client, owner)
            third = await audit.new(client, stranger)
            for cid, headers in ((first, owner), (second, owner), (third, stranger)):
                draft = await audit.setup_draft(client, headers, cid, audit.draft())
                (await client.post(f"/api/v1/conversations/{cid}/requirement/confirm", headers=headers, json=draft)).raise_for_status()
            status, job, _ = await audit.stream(client, owner, "POST", f"/api/v1/conversations/{first}/plan", stop_after=1)
            status2, repeat, _ = await audit.stream(client, owner, "POST", f"/api/v1/conversations/{first}/plan", stop_after=1)
            audit.record("生产队列重复提交复用任务", status == status2 == 200 and job == repeat)
            response = await client.post(f"/api/v1/conversations/{second}/plan", headers=owner)
            audit.record("同用户第二个任务被拒绝", response.status_code == 429, status=response.status_code)
            status, other_job, _ = await audit.stream(client, stranger, "POST", f"/api/v1/conversations/{third}/plan", stop_after=1)
            response = await client.post(f"/api/v1/conversations/{second}/plan", headers=owner)
            audit.record("全局任务上限生效", status == 200 and response.status_code == 429 and "任务较多" in response.json()["detail"])
            worker = start("worker")
            deadline = time.monotonic() + 25
            while True:
                state = (await client.get(f"/api/v1/conversations/{first}/plan/state", headers=owner)).json()
                if state["status"] == "running":
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Worker did not start job")
                await asyncio.sleep(.1)
            worker.kill()
            worker.wait(timeout=5)
            start("worker")
            started = time.monotonic()
            deadline = started + 150
            while True:
                states = [(await client.get(f"/api/v1/conversations/{cid}/plan/state", headers=headers)).json()
                          for cid, headers in ((first, owner), (third, stranger))]
                if all(state["status"] in {"completed", "failed"} for state in states):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Unattended recovery timed out")
                await asyncio.sleep(.5)
            plans = [(await client.get(f"/api/v1/conversations/{cid}/plans", headers=headers)).json()
                     for cid, headers in ((first, owner), (third, stranger))]
            # Separate SQLAlchemy engine is explicit: never query the source DB.
            from sqlalchemy import create_engine
            engine = create_engine(changes["database_url"])
            with engine.connect() as connection:
                attempts = connection.scalar(text("SELECT retry_count FROM planning_jobs WHERE id=:id"), {"id": job})
                completions = connection.scalar(text("SELECT COUNT(*) FROM planning_events WHERE job_id=:id AND event_type='plan_completed'"), {"id": job})
                active = connection.scalar(text("SELECT COUNT(*) FROM planning_jobs WHERE status IN ('queued','running')"))
            engine.dispose()
            audit.record("独立worker无SSE观察者故障接管", all(state["status"] == "completed" for state in states)
                         and all(len(plan) == 1 for plan in plans) and attempts >= 2 and completions == 1,
                         states=states, plan_counts=[len(plan) for plan in plans], attempts=attempts,
                         completion_events=completions, seconds=round(time.monotonic() - started, 2))
            audit.record("隔离库无任务遗留", active == 0, active_jobs=active)
            username = (await client.get("/api/v1/auth/me", headers=stranger)).json()["username"]
            codes = [(await client.post("/api/v1/auth/login", json={"username": username, "password": "wrong-qa-password"})).status_code for _ in range(3)]
            audit.record("真实Redis登录限流", codes == [401, 401, 429], status_codes=codes)
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        audit.results["temporary_processes_stopped"] = all(p.poll() is not None for p in children)
        audit.save()
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["audit", "serve", "worker"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--output", default="docs/acceptance/production-preflight-20261019.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    if args.mode in {"serve", "worker"}:
        configure(args.config)
        if args.mode == "serve":
            import uvicorn
            from backend.main import create_app
            uvicorn.run(create_app(), host="127.0.0.1", port=18083, log_level="critical", access_log=False)
        else:
            import run_planning_worker
            asyncio.run(run_planning_worker.run(argparse.Namespace(reconcile_seconds=1, reconcile_limit=10, block_seconds=.5)))
    else:
        asyncio.run(preflight(args.database, args.output))

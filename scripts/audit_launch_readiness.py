"""Bounded launch audit: own QA records, no changes to business configuration.

Uses real providers for two concurrent plans and one isolated failover job.
Only child servers started by this script are terminated. Never prints secrets.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from audit_planning_robustness import Audit
from backend.config import get_settings
from backend.database import open_session
from backend.database.tables import PlanningEventRow, PlanningJobRow, TravelPlanRow
from backend.main import create_app


class LaunchAudit(Audit):
    def configuration(self):
        settings = get_settings()
        try:
            settings.model_copy(update={"app_env": "production"}).validate_production_security()
            production_secret_ok = True
        except ValueError:
            production_secret_ok = False
        url = make_url(settings.database_url) if settings.database_url else None
        self.results["configuration"] = {
            "app_env": settings.app_env, "auth_required_in_file": settings.auth_required,
            "production_secret_guard_passed": production_secret_ok,
            "database_superuser_name": bool(url and url.username in {"root", "admin"}),
            "redis_configured": bool(settings.redis_url),
            "external_queue_enabled": settings.planning_queue_enabled,
            "alert_webhook_configured": bool(settings.alert_webhook_url),
            "token_expiry_minutes": settings.access_token_expire_minutes,
            "worker_stale_seconds": settings.planning_worker_stale_seconds,
        }
        self.record("生产密钥启动校验", production_secret_ok)
        app = create_app(initialize_database=False)

        def dependency_names(dependant):
            names = {getattr(dependant.call, "__name__", "")}
            for child in dependant.dependencies:
                names.update(dependency_names(child))
            return names

        routes = []
        def flattened_routes(items, inherited=None):
            inherited = inherited or set()
            for route in items:
                nested = getattr(route, "original_router", None)
                if nested is not None:
                    context = getattr(route, "include_context", None)
                    names = {getattr(item.dependency, "__name__", "")
                             for item in getattr(context, "dependencies", [])}
                    yield from flattened_routes(nested.routes, inherited | names)
                else:
                    yield route, inherited

        for route, inherited in flattened_routes(app.routes):
            if not hasattr(route, "dependant"):
                continue
            names = dependency_names(route.dependant) | inherited
            routes.append({"path": route.path, "methods": sorted(route.methods),
                           "user_auth": "get_current_user" in names,
                           "admin_auth": "require_admin" in names})
        self.results["route_auth_inventory"] = routes
        checkpoint = Path(settings.planning_checkpoint_path) if settings.planning_checkpoint_path else None
        if checkpoint and checkpoint.is_file():
            with sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            self.record("当前检查点数据库完整性", integrity == "ok")
        backups = ROOT / "data" / "backups"
        self.results["backup_scope"] = {
            "maintenance_json_count": len(list(backups.glob("*.json"))) if backups.exists() else 0,
            "full_database_restore_exercised": False,
            "note": "维护脚本的景点 JSON 备份不等于完整业务库备份；本轮不导出或恢复用户数据。",
        }
        self.save()

    async def security(self, client, owner, stranger):
        response = await client.get("/api/v1/auth/me", headers=owner)
        self.record("普通用户身份正常", response.status_code == 200 and response.json()["role"] == "user")
        for method, path, payload, expected in [
            ("GET", "/api/v1/conversations", None, 401),
            ("GET", "/api/v1/general-conversations", None, 401),
            ("GET", "/api/v1/admin/users", None, 401),
            ("POST", "/api/v1/retrieval/index?city=杭州", None, 401),
        ]:
            response = await client.request(method, path, json=payload)
            self.record("匿名保护 " + path, response.status_code == expected, status=response.status_code)
        for path in ("/api/v1/admin/users", "/api/v1/admin/knowledge/guides"):
            response = await client.get(path, headers=owner)
            self.record("普通用户拒绝后台 " + path, response.status_code == 403, status=response.status_code)
        token = owner["Authorization"].split(" ", 1)[1]
        version, payload, signature = token.split(".")
        import base64
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        decoded["role"] = "admin"
        modified = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
        for label, bad_token in [("损坏令牌", "invalid"), ("篡改管理员角色", f"{version}.{modified}.{signature}")]:
            response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + bad_token})
            self.record(label, response.status_code == 401, status=response.status_code)
        cid = await self.new(client, owner)
        for suffix in ("", "/plan/state", "/plans"):
            response = await client.get(f"/api/v1/conversations/{cid}{suffix}", headers=stranger)
            self.record("规划跨用户隔离 " + (suffix or "详情"), response.status_code == 404, status=response.status_code)
        general = await client.post("/api/v1/general-conversations", headers=owner)
        general.raise_for_status()
        gid = general.json()["id"]
        self.results["general_test_conversations"] = [gid]
        response = await client.get(f"/api/v1/general-conversations/{gid}", headers=stranger)
        self.record("普通会话跨用户隔离", response.status_code == 404, status=response.status_code)
        response = await client.options("/api/v1/conversations", headers={
            "Origin": "https://untrusted-launch-audit.invalid", "Access-Control-Request-Method": "GET"})
        self.record("未知跨域来源拒绝", response.status_code == 400
                    and not response.headers.get("access-control-allow-origin"), status=response.status_code)
        response = await client.get("/api/v1/cities/杭州/attractions?use_amap=false&limit=1")
        self.record("本地景点旁路要求登录", response.status_code == 401, status=response.status_code,
                    note="本次强制只读本地，未匿名调用地图；该接口默认开启外部详情补充。")
        response = await client.post("/api/v1/requirements/resolve", json={"text": "杭州玩一天，喜欢自然风光"})
        self.record("需求解析旁路要求登录", response.status_code == 401, status=response.status_code)
        response = await client.get("/metrics")
        self.results["operational_endpoints"] = {
            "anonymous_metrics_status": response.status_code,
            "anonymous_docs_status": (await client.get("/docs")).status_code,
            "transport": self.base.split(":", 1)[0],
            "security_headers_present": [name for name in (
                "strict-transport-security", "content-security-policy", "x-content-type-options",
                "x-frame-options") if name in response.headers],
        }
        login_codes = []
        for _ in range(6):
            response = await client.post("/api/v1/auth/login", json={
                "username": self.login_username, "password": "intentionally-wrong-qa-password"})
            login_codes.append(response.status_code)
        self.results["bounded_login_probe"] = {"attempts": 6, "status_codes": login_codes,
                                                "note": "只对本轮自建账号测试；不能据六次请求推断任意限流阈值。"}
        self.save()

    async def light_load(self, client, owner):
        semaphore = asyncio.Semaphore(10)

        async def one(index):
            async with semaphore:
                start = time.perf_counter()
                path = "/health" if index % 2 else "/api/v1/conversations?limit=1"
                try:
                    response = await client.get(path, headers=owner)
                    return {"status": response.status_code, "seconds": round(time.perf_counter() - start, 4)}
                except httpx.HTTPError as exc:
                    return {"status": type(exc).__name__, "seconds": round(time.perf_counter() - start, 4)}

        samples = await asyncio.gather(*(one(index) for index in range(40)))
        timings = sorted(item["seconds"] for item in samples)
        self.record("轻量混合接口10并发40请求", all(item["status"] == 200 for item in samples),
                    p50=timings[len(timings) // 2], p95=timings[int(len(timings) * .95) - 1],
                    max_seconds=timings[-1], samples=samples)

    async def failover(self, owner):
        processes = []
        ports = (18081, 18082)
        checkpoint = ROOT / "data" / "acceptance" / f"launch-failover-{uuid4().hex[:8]}.sqlite3"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        cid = None
        job_id = None
        try:
            for port in ports:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            for port in ports:
                processes.append(subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--isolated-server", str(port),
                     "--checkpoint", str(checkpoint)], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
            async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                for port in ports:
                    deadline = time.monotonic() + 25
                    while True:
                        try:
                            if (await client.get(f"http://127.0.0.1:{port}/health", timeout=1)).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise TimeoutError("Isolated server did not start")
                        await asyncio.sleep(.25)
                owner_client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports[0]}", timeout=120, trust_env=False)
                follower = httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports[1]}", timeout=120, trust_env=False)
                async with owner_client, follower:
                    cid = await self.new(owner_client, owner)
                    draft = await self.setup_draft(owner_client, owner, cid, self.draft())
                    confirmation = await owner_client.post(f"/api/v1/conversations/{cid}/requirement/confirm", headers=owner, json=draft)
                    confirmation.raise_for_status()
                    status, job_id, initial = await self.stream(owner_client, owner, "POST",
                                                               f"/api/v1/conversations/{cid}/plan", stop_after=2)
                    if status != 200 or not job_id:
                        raise RuntimeError("Isolated job did not start")
                    before = (await follower.get(f"/api/v1/conversations/{cid}/plan/state", headers=owner)).json()
                    processes[0].kill()
                    processes[0].wait(timeout=5)
                    cursor = max((event["id"] or 0 for event in initial), default=0)
                    started = time.monotonic()
                    async with asyncio.timeout(120):
                        status, replay_job, events = await self.stream(follower, owner, "GET",
                            f"/api/v1/conversations/{cid}/plan/events?after={cursor}&job_id={job_id}")
                    state = (await follower.get(f"/api/v1/conversations/{cid}/plan/state", headers=owner)).json()
                    plans = (await follower.get(f"/api/v1/conversations/{cid}/plans", headers=owner)).json()
                    with open_session() as session:
                        job = session.get(PlanningJobRow, job_id)
                        terminal_count = session.scalar(select(func.count()).select_from(PlanningEventRow).where(
                            PlanningEventRow.job_id == job_id, PlanningEventRow.event_type == "plan_completed"))
                        retries = job.retry_count
                    ids = [event["id"] for event in events if event["id"] is not None]
                    self.record("隔离进程强停后另一实例接管", before["status"] == "running" and status == 200
                                and state["status"] == "completed" and len(plans) == 1 and terminal_count == 1
                                and retries >= 2 and ids == sorted(set(ids)) and all(value > cursor for value in ids),
                                conversation=cid, job_id=job_id, before=before, after=state,
                                saved_plans=len(plans), completion_events=terminal_count, execution_attempts=retries,
                                recovery_seconds=round(time.monotonic() - started, 2), event_ids=ids,
                                note="独立端口、独立检查点、1秒心跳/6秒租约；恢复由客户端连接备用实例触发，不等于无观察者自动恢复。")
        except Exception as exc:
            self.record("隔离进程强停后另一实例接管", False, exception=type(exc).__name__,
                        conversation=cid, job_id=job_id)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            self.results["isolated_process_cleanup"] = {"all_stopped": all(p.poll() is not None for p in processes),
                                                         "checkpoint_file": str(checkpoint)}
            self.save()

    async def launch(self):
        self.configuration()
        async with httpx.AsyncClient(base_url=self.base, timeout=120, trust_env=False) as client:
            owner, owner_id = await self.account(client, "launch")
            stranger, stranger_id = await self.account(client, "other")
            self.results["test_user_ids"] = [owner_id, stranger_id]
            self.login_username = (await client.get("/api/v1/auth/me", headers=owner)).json()["username"]
            await self.security(client, owner, stranger)
            await self.light_load(client, owner)
            # Duplicate-submit mode expects an already saved confirmation
            # snapshot, just as the browser dialogue prepares one first.
            duplicate_cid = await self.new(client, owner)
            duplicate_draft = await self.setup_draft(client, owner, duplicate_cid, self.draft())
            await asyncio.gather(
                self.plan(client, owner, "双任务并发-杭州", duplicate_draft, cid=duplicate_cid, duplicate=True),
                self.plan(client, stranger, "双任务并发-绍兴", self.draft(["绍兴市"])),
            )
            await self.failover(owner)
            counts = []
            with open_session() as session:
                for cid in self.results["conversations"]:
                    jobs = list(session.scalars(select(PlanningJobRow).where(PlanningJobRow.conversation_id == cid)))
                    plans = session.scalar(select(func.count()).select_from(TravelPlanRow).where(TravelPlanRow.session_id == cid))
                    counts.append({"conversation": cid, "jobs": [row.status for row in jobs], "plans": plans})
            self.record("自建验收任务无排队执行遗留", all(status not in {"queued", "running"}
                        for item in counts for status in item["jobs"]), records=counts)
        self.results["finished"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        self.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/acceptance/launch-readiness-20261019.json")
    parser.add_argument("--isolated-server", type=int)
    parser.add_argument("--checkpoint")
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    if args.isolated_server:
        import uvicorn
        settings = get_settings()
        settings.auth_required = True
        settings.planning_queue_enabled = False
        settings.planning_heartbeat_seconds = 1
        settings.planning_worker_stale_seconds = 6
        settings.planning_checkpoint_path = args.checkpoint
        app = create_app(initialize_database=False)
        app.state.auth_bypass = False
        uvicorn.run(app, host="127.0.0.1", port=args.isolated_server, log_level="critical", access_log=False)
    else:
        asyncio.run(LaunchAudit(args.base_url, args.output).launch())

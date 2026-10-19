"""Opt-in live HTTP acceptance, using new accounts/conversations only.

Calls real configured providers. Never deletes records, changes configuration,
injects failures into the running service, or prints authentication secrets.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx


class Audit:
    def __init__(self, base, output):
        self.base = base.rstrip("/")
        self.output = Path(output)
        self.today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        self.results = {"date": str(self.today), "base_url": base, "cases": [], "conversations": []}

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, name, ok, **facts):
        self.results["cases"].append({"name": name, "passed": bool(ok), **facts})
        self.save()
        print(json.dumps({"name": name, "passed": bool(ok), **facts}, ensure_ascii=False), flush=True)

    async def account(self, client, suffix):
        response = await client.post("/api/v1/auth/register", json={
            "username": f"qa_plan_{self.today:%Y%m%d}_{uuid4().hex[:8]}_{suffix}",
            "password": secrets.token_urlsafe(20), "display_name": "规划健壮性验收",
        })
        response.raise_for_status()
        body = response.json()
        return {"Authorization": "Bearer " + body["access_token"]}, body["user"]["id"]

    async def new(self, client, headers):
        response = await client.post("/api/v1/conversations", headers=headers)
        response.raise_for_status()
        cid = response.json()["id"]
        self.results["conversations"].append(cid)
        self.save()
        return cid

    def draft(self, cities=None, days=1, **changes):
        result = dict(province="浙江省", cities=cities or ["杭州市"], days=days,
                      city_preference_confirmed=True, province_preference_confirmed=False,
                      interests=["自然"], preference_text="节奏轻松，不赶时间",
                      travel_scope="single_city" if days <= 3 else "multi_city",
                      start_date=str(self.today + timedelta(days=1)),
                      daily_start_time="09:00", daily_end_time="18:00", transport_mode="auto")
        return {**result, **changes}

    async def setup_draft(self, client, headers, cid, draft):
        response = await client.patch(f"/api/v1/conversations/{cid}/requirement", headers=headers, json=draft)
        response.raise_for_status()
        return response.json()["requirement"]

    async def stream(self, client, headers, method, path, *, body=None, stop_after=None):
        events = []
        async with client.stream(method, path, headers=headers, json=body) as response:
            status = response.status_code
            job = response.headers.get("X-Planning-Job-Id")
            if status != 200:
                raw = await response.aread()
                return status, job, [{"event": "http_error", "data": json.loads(raw)}]
            event, event_id, lines = "message", None, []
            async for line in response.aiter_lines():
                if not line:
                    if lines:
                        events.append({"id": event_id, "event": event, "data": json.loads("\n".join(lines))})
                        if stop_after and len(events) >= stop_after:
                            break
                    event, event_id, lines = "message", None, []
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("id:"):
                    event_id = int(line[3:].strip())
                elif line.startswith("data:"):
                    lines.append(line[5:].strip())
        return status, job, events

    async def negative_cases(self, client, owner, stranger):
        cid = await self.new(client, owner)
        response = await client.post(f"/api/v1/conversations/{cid}/plan", headers=owner)
        self.record("未确认不得启动", response.status_code == 409, status=response.status_code, conversation=cid)
        response = await client.post(f"/api/v1/conversations/{cid}/requirement/confirm", headers=owner, json={})
        self.record("不完整需求不得确认", response.status_code == 422, status=response.status_code)
        invalid = [
            ("零天", self.draft(days=0)), ("超上限八天", self.draft(days=8)),
            ("当天日期", self.draft(start_date=str(self.today))),
            ("过去日期", self.draft(start_date=str(self.today-timedelta(days=1)))),
            ("每日时间倒置", self.draft(daily_start_time="18:00", daily_end_time="09:00")),
            ("零分钟窗口", self.draft(daily_start_time="09:00", daily_end_time="09:00")),
            ("跨省双城", self.draft(["杭州市", "苏州市"], days=4)),
            ("两天双城", self.draft(["杭州市", "绍兴市"], days=2)),
            ("四天指定单城", self.draft(days=4)),
            ("超过三城市", self.draft(["杭州市", "绍兴市", "宁波市", "湖州市"], days=7)),
            ("未知城市", self.draft(["不存在的验收城市"])),
            ("未知字段注入", self.draft(user_id="other-user")),
        ]
        for name, payload in invalid:
            response = await client.patch(f"/api/v1/conversations/{cid}/requirement", headers=owner, json=payload)
            self.record(name, response.status_code == 422, status=response.status_code,
                        detail=response.json().get("detail"))
        state = (await client.get(f"/api/v1/conversations/{cid}", headers=owner)).json()
        job = (await client.get(f"/api/v1/conversations/{cid}/plan/state", headers=owner)).json()
        self.record("拒绝非法输入后草稿未污染且无任务", state["requirement"]["days"] is None and job is None)
        for path in [f"/api/v1/conversations/{cid}", f"/api/v1/conversations/{cid}/plan/state",
                     f"/api/v1/conversations/{cid}/plan/events", f"/api/v1/conversations/{cid}/plans"]:
            response = await client.get(path, headers=stranger)
            self.record("跨用户隔离 " + path.rsplit("/", 1)[-1], response.status_code == 404, status=response.status_code)
        for label, headers in [("未登录", {}), ("无效令牌", {"Authorization": "Bearer invalid-test-token"})]:
            response = await client.get(f"/api/v1/conversations/{cid}", headers=headers)
            self.record(label, response.status_code == 401, status=response.status_code)
        for payload in [{"message": "", "mode": "planning"}, {"message": "x"*2001, "mode": "planning"}]:
            response = await client.post(f"/api/v1/conversations/{cid}/messages", headers=owner, json=payload)
            self.record("消息长度边界 " + str(len(payload["message"])), response.status_code == 422, status=response.status_code)
        old = await self.setup_draft(client, owner, cid, self.draft())
        await self.setup_draft(client, owner, cid, {"days": 2})
        response = await client.post(f"/api/v1/conversations/{cid}/requirement/confirm", headers=owner, json=old)
        self.record("旧确认快照拒绝", response.status_code == 409, status=response.status_code)

    async def live_dialogue(self, client, owner):
        cid = await self.new(client, owner)
        started = time.monotonic()
        text = (f"【API健壮性验收】杭州玩1天，喜欢自然风光，节奏轻松不赶时间，"
                f"{self.today+timedelta(days=1)}出发，每天09:00到18:00。")
        status, _, events = await self.stream(client, owner, "POST", f"/api/v1/conversations/{cid}/messages",
                                             body={"message": text, "mode": "planning"})
        self.results["dialogue_events"] = events
        current = (await client.get(f"/api/v1/conversations/{cid}", headers=owner)).json()
        reply = "".join(e["data"].get("text", "") for e in events if e["event"] == "message_delta")
        offered = any(e["event"] == "suggestions" and "确认生成计划" in e["data"].get("options", []) for e in events)
        ok = (status == 200 and offered and current["requirement"]["cities"] == ["杭州市"]
              and current["requirement"]["days"] == 1 and not current["requirement_confirmed"] and "\\n" not in reply)
        self.record("真实Agent一轮需求与确认入口", ok, conversation=cid,
                    elapsed=round(time.monotonic()-started, 2), reply=reply)
        return cid, current["requirement"], ok

    @staticmethod
    def quality(plan, draft):
        itinerary = plan.get("itinerary", [])
        errors, cautions = [], []
        if len(itinerary) != draft["days"]:
            errors.append("行程天数不一致")
        if [d["day"] for d in itinerary] != list(range(1, draft["days"]+1)):
            errors.append("行程日编号不连续")
        seen, summary = set(), []
        for day in itinerary:
            expected_date = str(date.fromisoformat(draft["start_date"]) + timedelta(days=day["day"]-1))
            if day.get("date") != expected_date:
                errors.append(f"第{day['day']}天日期不连续")
            if draft.get("cities") and day["city"] not in draft["cities"]:
                errors.append(f"第{day['day']}天城市不在确认范围")
            schedule = day.get("schedule") or {}
            if not schedule or schedule.get("end_time", "99:99") > draft["daily_end_time"]:
                errors.append(f"第{day['day']}天完整时间表缺失或越界")
            previous = draft["daily_start_time"]
            for stop in day["attractions"]:
                if stop["poi_id"] in seen:
                    cautions.append(f"重复景点ID：{stop['poi_id']}")
                seen.add(stop["poi_id"])
                start, end = stop.get("start_time"), stop.get("end_time")
                if not start or not end or start < previous or end <= start or end > draft["daily_end_time"]:
                    errors.append(f"第{day['day']}天景点时间越界或重叠：{stop['name']}")
                previous = end or previous
            if len(day.get("routes", [])) != max(0, len(day["attractions"])-1):
                errors.append(f"第{day['day']}天内部路线数量不完整")
            all_legs = [*day.get("routes", []), *[day[key] for key in ("start_hotel_route", "hotel_route") if day.get(key)]]
            summary.append({"day": day["day"], "city": day["city"], "date": day.get("date"),
                            "stops": [{"name": s["name"], "poi_id": s["poi_id"], "start": s.get("start_time"),
                                       "end": s.get("end_time")} for s in day["attractions"]],
                            "schedule": day.get("schedule"),
                            "weather_status": (day.get("weather") or {}).get("status"),
                            "estimated_routes": sum(bool(r.get("estimated")) for r in day.get("routes", [])),
                            "estimated_all_route_legs": sum(bool(r.get("estimated")) for r in all_legs)})
        return {"hard_errors": errors, "cautions": cautions, "days": summary,
                "evaluation": plan.get("evaluation"), "notices": plan.get("notices")}

    async def plan(self, client, owner, name, draft, cid=None, duplicate=False, expect_failure=False):
        cid = cid or await self.new(client, owner)
        if not duplicate:
            draft = await self.setup_draft(client, owner, cid, draft)
        confirmed = await client.post(f"/api/v1/conversations/{cid}/requirement/confirm", headers=owner, json=draft)
        if confirmed.status_code != 200:
            self.record(name, False, conversation=cid, status=confirmed.status_code, stage="confirm", detail=confirmed.json())
            return
        started = time.monotonic()
        if duplicate:
            status, initial_job, initial = await self.stream(client, owner, "POST", f"/api/v1/conversations/{cid}/plan", stop_after=2)
            if status != 200:
                self.record(name, False, status=status, stage="initial_stream")
                return
            status, repeat_job, repeat = await self.stream(client, owner, "POST", f"/api/v1/conversations/{cid}/plan", stop_after=1)
            self.record("重复提交复用同一任务", status == 200 and repeat_job == initial_job,
                        conversation=cid, initial_job=initial_job, repeated_job=repeat_job)
            cursor = max((e["id"] or 0 for e in initial), default=0)
            status, job_id, events = await self.stream(client, owner, "GET", f"/api/v1/conversations/{cid}/plan/events?after={cursor}&job_id={initial_job}")
            ids = [e["id"] for e in events if e["id"]]
            self.record("断流后按游标接续", status == 200 and all(i > cursor for i in ids) and ids == sorted(set(ids)),
                        conversation=cid, cursor=cursor, event_ids=ids)
            events = initial + events
        else:
            status, job_id, events = await self.stream(client, owner, "POST", f"/api/v1/conversations/{cid}/plan")
        self.results.setdefault("plan_events", {})[name] = events
        state = (await client.get(f"/api/v1/conversations/{cid}/plan/state", headers=owner)).json()
        saved = (await client.get(f"/api/v1/conversations/{cid}/plans", headers=owner)).json()
        completed = next((e["data"] for e in events if e["event"] == "plan_completed"), None)
        failure = next((e["data"] for e in events if e["event"] == "planning_error"), None)
        quality = self.quality(completed["plan"], draft) if completed else None
        if expect_failure:
            ok = bool(failure and state and state["status"] == "failed" and not saved)
        else:
            ok = bool(status == 200 and completed and state and state["status"] == "completed"
                      and len(saved) == 1 and not quality["hard_errors"])
        self.record(name, ok, conversation=cid, job_id=job_id, elapsed=round(time.monotonic()-started, 2),
                    terminal_state=state, saved_plan_count=len(saved), failure=failure, quality=quality)

    async def run(self):
        async with httpx.AsyncClient(base_url=self.base, timeout=httpx.Timeout(120, connect=10), trust_env=False) as client:
            (await client.get("/health")).raise_for_status()
            owner, owner_id = await self.account(client, "owner")
            stranger, stranger_id = await self.account(client, "other")
            self.results["test_user_ids"] = [owner_id, stranger_id]
            self.save()
            await self.negative_cases(client, owner, stranger)
            try:
                cid, draft, ok = await self.live_dialogue(client, owner)
                if ok:
                    await self.plan(client, owner, "正常单日完整链路", draft, cid=cid, duplicate=True)
            except Exception as exc:
                self.record("正常单日完整链路", False, exception=type(exc).__name__)
            for name, draft, fail in [
                ("正常四天双城市", self.draft(["杭州市", "绍兴市"], days=4), False),
                ("七天三城市上边界", self.draft(["杭州市", "绍兴市", "宁波市"], days=7), False),
                ("一分钟窗口不可行任务", self.draft(daily_end_time="09:01"), True),
            ]:
                try:
                    async with asyncio.timeout(360):
                        await self.plan(client, owner, name, draft, expect_failure=fail)
                except Exception as exc:
                    self.record(name, False, exception=type(exc).__name__)
            self.results["finished"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            self.save()

    async def input_fixes(self):
        async with httpx.AsyncClient(base_url=self.base, timeout=httpx.Timeout(120, connect=10), trust_env=False) as client:
            owner, owner_id = await self.account(client, "fix")
            self.results["test_user_ids"] = [owner_id]
            cid = await self.new(client, owner)
            path = f"/api/v1/conversations/{cid}"
            before = (await client.get(path, headers=owner)).json()
            for name, message in [("空格", "   "), ("制表换行", "\t\r\n"), ("全角空白", "\u3000\u00a0")]:
                response = await client.post(path + "/messages", headers=owner, json={"message": message})
                self.record("空白消息拒绝-" + name, response.status_code == 422
                            and response.headers.get("content-type", "").startswith("application/json"),
                            status=response.status_code)
            for value in (True, False):
                for action in ("patch", "confirm"):
                    response = (await client.patch(path + "/requirement", headers=owner, json={"days": value})
                                if action == "patch" else await client.post(path + "/requirement/confirm", headers=owner, json={"days": value}))
                    self.record(f"布尔天数拒绝-{action}-{value}", response.status_code == 422,
                                status=response.status_code)
            after = (await client.get(path, headers=owner)).json()
            state = (await client.get(path + "/plan/state", headers=owner)).json()
            plans = (await client.get(path + "/plans", headers=owner)).json()
            self.record("非法输入未写入消息草稿或启动任务", before == after and state is None and not plans)
            for value in (1, 2, 3):
                response = await client.patch(path + "/requirement", headers=owner, json={"days": value})
                self.record(f"合法整数天数-{value}", response.status_code == 200
                            and response.json().get("requirement", {}).get("days") == value,
                            status=response.status_code)
            await self.live_dialogue(client, owner)
            self.results["finished"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            self.save()

    async def extras(self):
        async with httpx.AsyncClient(base_url=self.base, timeout=httpx.Timeout(120, connect=10), trust_env=False) as client:
            owner, owner_id = await self.account(client, "extra")
            self.results["test_user_ids"] = [owner_id]
            cid = await self.new(client, owner)
            for name, payload in [("纯空白消息", {"message": "   ", "mode": "planning"}),
                                  ("普通模式误投规划接口", {"message": "你好", "mode": "general"})]:
                try:
                    response = await client.post(f"/api/v1/conversations/{cid}/messages", headers=owner, json=payload)
                    self.record(name, response.status_code in (400, 409, 422), status=response.status_code,
                                body=response.text[:700])
                except Exception as exc:
                    self.record(name, False, exception=type(exc).__name__)
            response = await client.post(f"/api/v1/conversations/{cid}/messages", headers={**owner, "Content-Type": "application/json"}, content="{")
            self.record("畸形JSON", response.status_code == 422, status=response.status_code)
            for name, value in [("非整数天数", 1.5), ("布尔值冒充天数", True)]:
                response = await client.patch(f"/api/v1/conversations/{cid}/requirement", headers=owner, json={"days": value})
                self.record(name, response.status_code == 422, status=response.status_code,
                            stored_days=response.json().get("requirement", {}).get("days"))
            # Keep failures from one request from silently contaminating subsequent cases.
            await client.patch(f"/api/v1/conversations/{cid}/requirement", headers=owner, json={"days": None})
            await self.plan(client, owner, "远期日期无预报仍可完成", self.draft(start_date=str(self.today+timedelta(days=30))))
            failed_cid = await self.new(client, owner)
            await self.plan(client, owner, "失败后恢复-先不可行", self.draft(daily_end_time="09:01"), cid=failed_cid, expect_failure=True)
            await self.plan(client, owner, "失败后恢复-修正后重试", self.draft(), cid=failed_cid)
            self.results["finished"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            self.save()
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="docs/acceptance/planning-robustness-20261019.json")
    parser.add_argument("--phase", choices=["main", "extras", "input-fixes"], default="main")
    args = parser.parse_args()
    audit = Audit(args.base_url, args.output)
    phases = {"main": audit.run, "extras": audit.extras, "input-fixes": audit.input_fixes}
    asyncio.run(phases[args.phase]())

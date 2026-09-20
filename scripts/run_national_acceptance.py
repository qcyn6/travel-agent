"""Run a comparable single-city acceptance matrix across the destination catalog.

The matrix deliberately uses the same one-day requirement for every selected
representative city.  It measures the real external-data path instead of only
checking whether a city exists in the catalog.  Each standalone request creates
an anonymous conversation, so the script deletes that conversation after the
case finishes and does not leave acceptance history in the sidebar.

Examples::

    python scripts/run_national_acceptance.py --dry-run
    python scripts/run_national_acceptance.py --limit 5
    python scripts/run_national_acceptance.py --city 成都市 --city 西安市
    python scripts/run_national_acceptance.py --output data/acceptance/chengdu.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database.catalog import (  # noqa: E402
    CATALOG,
    CITY_ALIASES,
    PROVINCE_ALIASES,
)
from backend.models.travel_plan import TravelPlanResponse  # noqa: E402


@dataclass(frozen=True)
class RepresentativeCity:
    province: str
    city: str


# One city per catalog province.  These are deliberately stable so that a
# report can be compared across runs; this is not intended to be a ranking of
# the best tourist destination in each province.
REPRESENTATIVE_CITIES: tuple[RepresentativeCity, ...] = (
    RepresentativeCity("北京市", "北京市"),
    RepresentativeCity("天津市", "天津市"),
    RepresentativeCity("河北省", "石家庄市"),
    RepresentativeCity("山西省", "太原市"),
    RepresentativeCity("内蒙古自治区", "呼和浩特市"),
    RepresentativeCity("辽宁省", "沈阳市"),
    RepresentativeCity("吉林省", "长春市"),
    RepresentativeCity("黑龙江省", "哈尔滨市"),
    RepresentativeCity("上海市", "上海市"),
    RepresentativeCity("江苏省", "南京市"),
    RepresentativeCity("浙江省", "杭州市"),
    RepresentativeCity("安徽省", "合肥市"),
    RepresentativeCity("福建省", "福州市"),
    RepresentativeCity("江西省", "南昌市"),
    RepresentativeCity("山东省", "济南市"),
    RepresentativeCity("河南省", "郑州市"),
    RepresentativeCity("湖北省", "武汉市"),
    RepresentativeCity("湖南省", "长沙市"),
    RepresentativeCity("广东省", "广州市"),
    RepresentativeCity("广西壮族自治区", "南宁市"),
    RepresentativeCity("海南省", "海口市"),
    RepresentativeCity("重庆市", "重庆市"),
    RepresentativeCity("四川省", "成都市"),
    RepresentativeCity("贵州省", "贵阳市"),
    RepresentativeCity("云南省", "昆明市"),
    RepresentativeCity("西藏自治区", "拉萨市"),
    RepresentativeCity("陕西省", "西安市"),
    RepresentativeCity("甘肃省", "兰州市"),
    RepresentativeCity("青海省", "西宁市"),
    RepresentativeCity("宁夏回族自治区", "银川市"),
    RepresentativeCity("新疆维吾尔自治区", "乌鲁木齐市"),
    RepresentativeCity("台湾省", "台北市"),
    RepresentativeCity("香港特别行政区", "香港特别行政区"),
    RepresentativeCity("澳门特别行政区", "澳门特别行政区"),
)


def _catalog_city_names() -> dict[str, set[str]]:
    return {
        province["name"]: {city[0] for city in province["cities"]}
        for province in CATALOG
    }


def validate_representative_matrix() -> None:
    """Fail early when the fixed matrix drifts away from the catalog."""

    catalog_names = _catalog_city_names()
    catalog_provinces = set(catalog_names)
    matrix_provinces = {item.province for item in REPRESENTATIVE_CITIES}
    if matrix_provinces != catalog_provinces:
        missing = sorted(catalog_provinces - matrix_provinces)
        extra = sorted(matrix_provinces - catalog_provinces)
        raise RuntimeError(
            f"代表城市矩阵与全国目录省级范围不一致：missing={missing}, extra={extra}"
        )
    if len(REPRESENTATIVE_CITIES) != len(matrix_provinces):
        raise RuntimeError("代表城市矩阵必须保证每个省级地区恰好一个城市")
    for item in REPRESENTATIVE_CITIES:
        if item.city not in catalog_names[item.province]:
            raise RuntimeError(f"代表城市不在目录中：{item.province}/{item.city}")


def select_cases(
    *,
    cities: Iterable[str] = (),
    provinces: Iterable[str] = (),
    limit: int | None = None,
) -> list[RepresentativeCity]:
    raw_city_filter = {item.strip() for item in cities if item.strip()}
    raw_province_filter = {item.strip() for item in provinces if item.strip()}
    city_filter = {
        CITY_ALIASES.get(item, (item, ""))[0] for item in raw_city_filter
    }
    province_filter = {
        PROVINCE_ALIASES.get(item, item) for item in raw_province_filter
    }
    selected = [
        item
        for item in REPRESENTATIVE_CITIES
        if (not city_filter or item.city in city_filter)
        and (not province_filter or item.province in province_filter)
    ]
    if raw_city_filter:
        known_cities = {item.city for item in REPRESENTATIVE_CITIES}
        unknown = sorted(city_filter - known_cities)
        if unknown:
            raise ValueError(f"代表城市矩阵中不存在：{', '.join(unknown)}")
    if raw_province_filter:
        known_provinces = {item.province for item in REPRESENTATIVE_CITIES}
        unknown = sorted(province_filter - known_provinces)
        if unknown:
            raise ValueError(f"代表城市矩阵中不存在省级地区：{', '.join(unknown)}")
    if not selected:
        raise ValueError("没有匹配到任何验收城市")
    return selected[:limit] if limit is not None else selected


def default_start_date() -> date:
    """Keep the default date inside the short-range weather forecast window."""

    return date.today() + timedelta(days=3)


def build_request(case: RepresentativeCity, start_date: date) -> dict[str, Any]:
    return {
        "text": (
            f"{start_date.year}年{start_date.month}月{start_date.day}日去"
            f"{case.city}玩一天，喜欢自然、历史文化和美食，"
            "希望行程轻松，尽量不要太赶。"
        ),
        "travel_scope": "single_city",
    }


def _issue_key(issue: Any) -> str:
    return f"{issue.severity}:{issue.type}"


def summarize_plan(result: TravelPlanResponse) -> dict[str, Any]:
    """Extract comparable operational and quality signals from one plan."""

    plan = result.plan
    itinerary = plan.itinerary
    routes = [route for day in itinerary for route in day.routes]
    stops = [stop for day in itinerary for stop in day.attractions]
    weather_statuses = Counter(
        day.weather.status for day in itinerary if day.weather is not None
    )
    schedule_statuses = Counter(
        day.schedule.status for day in itinerary if day.schedule is not None
    )
    route_sources = Counter(route.source for route in routes)
    notices = Counter(notice.code for notice in plan.notices)
    issues = Counter(_issue_key(issue) for issue in (plan.evaluation.issues if plan.evaluation else []))
    return {
        "requirement_province": result.requirement.province,
        "requirement_cities": result.requirement.cities,
        "requirement_days": result.requirement.days,
        "requirement_start_date": (
            result.requirement.start_date.isoformat()
            if result.requirement.start_date
            else None
        ),
        "itinerary_days": len(itinerary),
        "attraction_count": len(stops),
        "route_count": len(routes),
        "route_sources": dict(sorted(route_sources.items())),
        "weather_statuses": dict(sorted(weather_statuses.items())),
        "schedule_statuses": dict(sorted(schedule_statuses.items())),
        "replan_count": plan.replan_count,
        "replan_limit_reached": plan.replan_limit_reached,
        "partial_knowledge_cities": plan.partial_knowledge_cities,
        "evaluation_passed": plan.evaluation.passed if plan.evaluation else None,
        "issues": dict(sorted(issues.items())),
        "notice_codes": dict(sorted(notices.items())),
    }


def validate_plan_shape(
    result: TravelPlanResponse,
    case: RepresentativeCity,
    start_date: date,
) -> list[str]:
    """Validate contract and destination identity without judging quality."""

    errors: list[str] = []
    if result.requirement.province != case.province:
        errors.append(
            f"省份标准化为 {result.requirement.province!r}，预期 {case.province!r}"
        )
    if result.requirement.cities != [case.city]:
        errors.append(
            f"城市标准化为 {result.requirement.cities!r}，预期 {[case.city]!r}"
        )
    if result.requirement.days != 1:
        errors.append(f"需求天数为 {result.requirement.days!r}，预期 1")
    if result.requirement.start_date != start_date:
        errors.append(
            f"出发日期为 {result.requirement.start_date!r}，预期 {start_date!r}"
        )
    if [stay.city for stay in result.plan.city_stays] != [case.city]:
        errors.append(
            f"方案城市为 {[stay.city for stay in result.plan.city_stays]!r}，预期 {[case.city]!r}"
        )
    if len(result.plan.itinerary) != 1:
        errors.append(f"行程天数为 {len(result.plan.itinerary)}，预期 1")
    if result.plan.evaluation is None:
        errors.append("缺少最终评估结果")
    for day in result.plan.itinerary:
        if day.city != case.city:
            errors.append(f"Day {day.day} 城市为 {day.city!r}，预期 {case.city!r}")
        if day.date != start_date:
            errors.append(f"Day {day.day} 日期为 {day.date!r}，预期 {start_date!r}")
        if len(day.routes) != max(0, len(day.attractions) - 1):
            errors.append(f"Day {day.day} 路线数量与景点数量不匹配")
        for stop in day.attractions:
            if not stop.poi_id:
                errors.append(f"Day {day.day} 存在缺少 poi_id 的景点")
            if not stop.start_time or not stop.end_time:
                errors.append(f"Day {day.day} 存在缺少起止时间的景点")
    return errors


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or "空响应"
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])[:500]
    return json.dumps(payload, ensure_ascii=False)[:500]


def run_case(
    client: httpx.Client,
    case: RepresentativeCity,
    start_date: date,
) -> dict[str, Any]:
    started = time.perf_counter()
    conversation_id: str | None = None
    record: dict[str, Any] = {
        **asdict(case),
        "start_date": start_date.isoformat(),
        "status": "error",
        "http_status": None,
        "elapsed_seconds": None,
        "conversation_deleted": None,
        "errors": [],
    }
    try:
        response = client.post("/api/v1/travel/plan", json=build_request(case, start_date))
        record["http_status"] = response.status_code
        conversation_id = response.headers.get("x-conversation-id")
        if response.status_code != 200:
            record["errors"] = [f"HTTP {response.status_code}: {_error_detail(response)}"]
            return record
        try:
            result = TravelPlanResponse.model_validate(response.json())
        except Exception as exc:
            record["errors"] = [f"响应结构无效：{type(exc).__name__}: {exc}"]
            return record

        shape_errors = validate_plan_shape(result, case, start_date)
        record["signals"] = summarize_plan(result)
        record["errors"] = shape_errors
        if shape_errors:
            record["status"] = "structure_error"
        elif result.plan.evaluation is not None and result.plan.evaluation.passed:
            record["status"] = "passed"
        else:
            record["status"] = "needs_confirmation"
        return record
    except httpx.HTTPError as exc:
        record["errors"] = [f"请求失败：{type(exc).__name__}: {exc}"]
        return record
    finally:
        record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        if conversation_id:
            try:
                cleanup = client.delete(f"/api/v1/conversations/{conversation_id}")
                record["conversation_deleted"] = cleanup.status_code in (204, 404)
                if not record["conversation_deleted"]:
                    record["errors"].append(
                        f"临时会话清理失败：HTTP {cleanup.status_code}"
                    )
            except httpx.HTTPError as exc:
                record["conversation_deleted"] = False
                record["errors"].append(f"临时会话清理请求失败：{exc}")


def summarize_matrix(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(record["status"] for record in records)
    issue_counts: Counter[str] = Counter()
    notice_counts: Counter[str] = Counter()
    route_sources: Counter[str] = Counter()
    weather_statuses: Counter[str] = Counter()
    schedule_statuses: Counter[str] = Counter()
    total_attractions = 0
    elapsed_values: list[float] = []
    for record in records:
        elapsed = record.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)):
            elapsed_values.append(float(elapsed))
        signals = record.get("signals") or {}
        total_attractions += int(signals.get("attraction_count") or 0)
        issue_counts.update(signals.get("issues") or {})
        notice_counts.update(signals.get("notice_codes") or {})
        route_sources.update(signals.get("route_sources") or {})
        weather_statuses.update(signals.get("weather_statuses") or {})
        schedule_statuses.update(signals.get("schedule_statuses") or {})
    return {
        "total": len(records),
        "statuses": dict(sorted(statuses.items())),
        "http_success": sum(record.get("http_status") == 200 for record in records),
        "structure_success": sum(
            record.get("status") in {"passed", "needs_confirmation"}
            for record in records
        ),
        "evaluator_passed": statuses.get("passed", 0),
        "needs_confirmation": statuses.get("needs_confirmation", 0),
        "errors": sum(
            record.get("status") in {"error", "structure_error"}
            for record in records
        ),
        "cleanup_failures": sum(
            record.get("conversation_deleted") is False for record in records
        ),
        "total_attractions": total_attractions,
        "average_elapsed_seconds": (
            round(sum(elapsed_values) / len(elapsed_values), 3)
            if elapsed_values
            else None
        ),
        "max_elapsed_seconds": max(elapsed_values) if elapsed_values else None,
        "issues": dict(issue_counts.most_common()),
        "notice_codes": dict(notice_counts.most_common()),
        "route_sources": dict(route_sources.most_common()),
        "weather_statuses": dict(weather_statuses.most_common()),
        "schedule_statuses": dict(schedule_statuses.most_common()),
    }


def write_report(path: Path, *, start_date: date, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "start_date": start_date.isoformat(),
        "matrix": [asdict(item) for item in REPRESENTATIVE_CITIES],
        "summary": summarize_matrix(records),
        "results": records,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("data") / "acceptance" / f"national-matrix-{timestamp}.json"


def _print_record(index: int, total: int, record: dict[str, Any]) -> None:
    status = record["status"]
    elapsed = record.get("elapsed_seconds")
    elapsed_text = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else "-"
    signals = record.get("signals") or {}
    details = [
        f"{record['province']}/{record['city']}",
        status,
        elapsed_text,
    ]
    if signals:
        details.extend(
            [
                f"景点 {signals.get('attraction_count', 0)}",
                f"路线 {signals.get('route_sources', {})}",
                f"重规划 {signals.get('replan_count', 0)}",
            ]
        )
    if record.get("errors"):
        details.append("；".join(record["errors"][:2]))
    print(f"[{index}/{total}] " + " | ".join(details), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="全国代表城市真实规划验收矩阵")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument(
        "--date",
        dest="start_date",
        type=date.fromisoformat,
        default=default_start_date(),
        help="统一出发日期，格式 YYYY-MM-DD；默认使用未来 3 天",
    )
    parser.add_argument("--limit", type=int, default=None, help="只运行矩阵前 N 个城市")
    parser.add_argument("--city", action="append", default=[], help="只运行指定代表城市，可重复")
    parser.add_argument("--province", action="append", default=[], help="只运行指定省级地区，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只检查矩阵与目录，不调用外部服务")
    parser.add_argument("--output", type=Path, default=None, help="JSON 报告输出路径")
    args = parser.parse_args()

    validate_representative_matrix()
    cases = select_cases(cities=args.city, provinces=args.province, limit=args.limit)
    print(f"代表城市矩阵：{len(REPRESENTATIVE_CITIES)} 个省级地区，当前运行 {len(cases)} 个城市", flush=True)
    print(f"统一出发日期：{args.start_date.isoformat()}", flush=True)
    if args.dry_run:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case.province}/{case.city} | catalog-ok", flush=True)
        print("干跑通过：代表城市均存在于全国目录", flush=True)
        return

    records: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        health = client.get("/health")
        health.raise_for_status()
        print(f"服务里程碑：{health.json().get('milestone')}", flush=True)
        for index, case in enumerate(cases, start=1):
            record = run_case(client, case, args.start_date)
            records.append(record)
            _print_record(index, len(cases), record)

    output = args.output or _default_output_path()
    write_report(output, start_date=args.start_date, records=records)
    summary = summarize_matrix(records)
    print("验收汇总：" + json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"报告已写入：{output.resolve()}", flush=True)

    if summary["errors"] or summary["cleanup_failures"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

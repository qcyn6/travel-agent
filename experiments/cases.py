from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backend.models.city_stay import CityStay
from backend.models.planning import PlanningCandidate
from backend.models.requirement import TravelRequirement


@dataclass(frozen=True)
class ExperimentCityCase:
    city_stay: CityStay
    candidates: tuple[PlanningCandidate, ...]
    route_minutes: dict[tuple[str, str], int | None]
    estimated_pairs: frozenset[tuple[str, str]] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    title: str
    requirement: TravelRequirement
    cities: tuple[ExperimentCityCase, ...]


_EXPERIMENT_DATE = date(2026, 10, 1)


def make_experiment_cases() -> tuple[ExperimentCase, ...]:
    """Return the frozen, database-independent experiment matrix."""

    return (
        _single_city_case(
            "hangzhou-1d",
            "杭州 1 天自然摄影",
            city="杭州市",
            days=1,
            interests=["自然", "摄影"],
        ),
        _single_city_case(
            "beijing-2d",
            "北京 2 天历史文化",
            city="北京市",
            days=2,
            interests=["历史文化", "人文"],
        ),
        _single_city_case(
            "chengdu-3d",
            "成都 3 天美食休闲",
            city="成都市",
            days=3,
            interests=["美食", "休闲"],
        ),
        _multi_city_case(
            "yunnan-4d",
            "云南 4 天同省多城市",
            cities=(
                ("昆明市", 2, 102.72),
                ("大理市", 2, 100.23),
            ),
            interests=["自然", "古城"],
        ),
        _multi_city_case(
            "yunnan-7d",
            "云南 7 天同省多城市",
            cities=(
                ("昆明市", 2, 102.72),
                ("大理市", 3, 100.23),
                ("丽江市", 2, 100.23),
            ),
            interests=["自然", "古城", "摄影"],
        ),
        _single_city_case(
            "hangzhou-short-window",
            "杭州短时间窗口",
            city="杭州市",
            days=1,
            interests=["自然"],
            start_time="10:00",
            end_time="13:00",
        ),
        _single_city_case(
            "hangzhou-route-unavailable",
            "杭州路线不可用降级",
            city="杭州市",
            days=1,
            interests=["自然", "摄影"],
            unavailable_pair_index=(0, 2),
        ),
        _single_city_case(
            "hangzhou-estimated-route",
            "杭州估算路线降级",
            city="杭州市",
            days=1,
            interests=["自然", "摄影"],
            estimated_pair_index=(0, 2),
        ),
    )


def _single_city_case(
    case_id: str,
    title: str,
    *,
    city: str,
    days: int,
    interests: list[str],
    start_time: str = "08:00",
    end_time: str = "21:00",
    unavailable_pair_index: tuple[int, int] | None = None,
    estimated_pair_index: tuple[int, int] | None = None,
) -> ExperimentCase:
    city_case = _build_city_case(city, days=days, longitude_base=_city_longitude(city))
    route_minutes = dict(city_case.route_minutes)
    estimated_pairs: set[tuple[str, str]] = set(city_case.estimated_pairs)
    if unavailable_pair_index is not None:
        pair = _pair_for_indices(city_case.candidates, unavailable_pair_index)
        route_minutes[pair] = None
    if estimated_pair_index is not None:
        pair = _pair_for_indices(city_case.candidates, estimated_pair_index)
        estimated_pairs.add(pair)
    city_case = ExperimentCityCase(
        city_stay=city_case.city_stay,
        candidates=city_case.candidates,
        route_minutes=route_minutes,
        estimated_pairs=frozenset(estimated_pairs),
    )
    requirement = _requirement(
        province=_province_for_city(city),
        cities=[city],
        days=days,
        interests=interests,
        start_time=start_time,
        end_time=end_time,
        scope="single_city",
    )
    return ExperimentCase(case_id, title, requirement, (city_case,))


def _multi_city_case(
    case_id: str,
    title: str,
    *,
    cities: tuple[tuple[str, int, float], ...],
    interests: list[str],
) -> ExperimentCase:
    city_cases = tuple(
        _build_city_case(city, days=days, longitude_base=longitude)
        for city, days, longitude in cities
    )
    city_names = [city for city, _, _ in cities]
    requirement = _requirement(
        province="云南省",
        cities=city_names,
        days=sum(days for _, days, _ in cities),
        interests=interests,
        start_time="08:00",
        end_time="21:00",
        scope="multi_city",
    )
    return ExperimentCase(case_id, title, requirement, city_cases)


def _build_city_case(city: str, *, days: int, longitude_base: float) -> ExperimentCityCase:
    candidates = tuple(
        _candidate(
            city,
            index,
            name,
            longitude=longitude_base + offset,
            latitude=30.20 + index * 0.025,
            preference_score=preference_score,
            semantic_score=semantic_score,
            keyword_score=keyword_score,
            tags=tags,
            duration=duration,
        )
        for index, (
            name,
            offset,
            preference_score,
            semantic_score,
            keyword_score,
            tags,
            duration,
        ) in enumerate(
            (
                ("中心湖景", 0.000, 0.96, 0.88, 1.00, ["自然", "摄影"], 100),
                ("城市古迹", 0.012, 0.84, 0.94, 0.84, ["历史文化", "人文"], 90),
                ("远郊山景", 0.180, 0.91, 0.97, 0.72, ["自然", "摄影"], 110),
                ("街区美食", 0.025, 0.78, 0.76, 0.95, ["美食", "休闲"], 90),
                ("古镇慢游", 0.045, 0.73, 0.70, 0.76, ["古城", "休闲"], 120),
            ),
            start=1,
        )
    )
    route_minutes: dict[tuple[str, str], int | None] = {}
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            distance = abs((left.longitude or 0) - (right.longitude or 0))
            duration = max(8, round(distance * 260))
            route_minutes[(left.poi_id, right.poi_id)] = duration
            route_minutes[(right.poi_id, left.poi_id)] = duration
    return ExperimentCityCase(
        city_stay=CityStay(city=city, days=days),
        candidates=candidates,
        route_minutes=route_minutes,
    )


def _candidate(
    city: str,
    index: int,
    name: str,
    *,
    longitude: float,
    latitude: float,
    preference_score: float,
    semantic_score: float,
    keyword_score: float,
    tags: list[str],
    duration: int,
) -> PlanningCandidate:
    return PlanningCandidate(
        poi_id=f"exp-{city}-{index}",
        name=f"{city}{name}",
        city=city,
        category="实验景点",
        interest_tags=tags,
        longitude=longitude,
        latitude=latitude,
        description=f"{city}{name}固定实验候选。",
        visit_duration_minutes=duration,
        tag_match_count=len(tags),
        semantic_score=semantic_score,
        keyword_score=keyword_score,
        preference_score=preference_score,
    )


def _requirement(
    *,
    province: str,
    cities: list[str],
    days: int,
    interests: list[str],
    start_time: str,
    end_time: str,
    scope: str,
) -> TravelRequirement:
    return TravelRequirement(
        province=province,
        cities=cities,
        days=days,
        interests=interests,
        travel_scope=scope,
        start_date=_EXPERIMENT_DATE,
        daily_start_time=start_time,
        daily_end_time=end_time,
        transport_mode="transit",
    )


def _pair_for_indices(
    candidates: tuple[PlanningCandidate, ...],
    indexes: tuple[int, int],
) -> tuple[str, str]:
    return candidates[indexes[0]].poi_id, candidates[indexes[1]].poi_id


def _city_longitude(city: str) -> float:
    return {
        "杭州市": 120.14,
        "北京市": 116.40,
        "成都市": 104.07,
    }.get(city, 102.72)


def _province_for_city(city: str) -> str:
    return {
        "杭州市": "浙江省",
        "北京市": "北京市",
        "成都市": "四川省",
    }.get(city, "云南省")


__all__ = ["ExperimentCase", "ExperimentCityCase", "make_experiment_cases"]

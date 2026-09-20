from datetime import date, timedelta

from scripts.run_national_acceptance import (
    REPRESENTATIVE_CITIES,
    RepresentativeCity,
    build_request,
    select_cases,
    validate_plan_shape,
    validate_representative_matrix,
)
from backend.models.travel_plan import TravelPlanResponse


def test_representative_matrix_covers_every_catalog_province():
    validate_representative_matrix()
    assert len(REPRESENTATIVE_CITIES) == 34
    assert len({item.province for item in REPRESENTATIVE_CITIES}) == 34


def test_select_cases_supports_city_and_province_filters():
    assert select_cases(cities=["成都"])[0] == RepresentativeCity("四川省", "成都市")
    selected = select_cases(provinces=["浙江省", "云南省"])
    assert selected == [
        RepresentativeCity("浙江省", "杭州市"),
        RepresentativeCity("云南省", "昆明市"),
    ]


def test_build_request_uses_reproducible_single_city_workload():
    request = build_request(
        RepresentativeCity("四川省", "成都市"),
        date(2026, 9, 20),
    )
    assert request == {
        "text": "2026年9月20日去成都市玩一天，喜欢自然、历史文化和美食，希望行程轻松，尽量不要太赶。",
        "travel_scope": "single_city",
    }


def test_validate_plan_shape_accepts_expected_one_day_plan():
    start_date = date.today() + timedelta(days=1)
    start_date_text = start_date.isoformat()
    body = {
        "requirement": {
            "province": "四川省",
            "cities": ["成都市"],
            "days": 1,
            "interests": ["自然"],
            "preference_text": "行程轻松",
            "travel_scope": "single_city",
            "start_date": start_date_text,
            "transport_mode": "auto",
        },
        "plan": {
            "province": "四川省",
            "city_stays": [{"city": "成都市", "days": 1}],
            "city_plans": [
                {
                    "city": "成都市",
                    "days": [{"day": 1, "attractions": ["宽窄巷子"], "description": "这是一个足够长的行程说明。"}],
                }
            ],
            "itinerary": [
                {
                    "day": 1,
                    "city": "成都市",
                    "city_adcode": "510100",
                    "city_day": 1,
                    "date": start_date_text,
                    "attractions": [
                        {
                            "poi_id": "p1",
                            "name": "宽窄巷子",
                            "city": "成都市",
                            "visit_duration_minutes": 120,
                            "start_time": "09:00",
                            "end_time": "11:00",
                        }
                    ],
                    "routes": [],
                    "weather": {
                        "city": "成都市",
                            "date": start_date_text,
                        "status": "unavailable",
                        "source": "none",
                    },
                    "schedule": {
                        "start_time": "09:00",
                        "end_time": "11:00",
                        "visit_minutes": 120,
                        "route_minutes": 0,
                        "meal_minutes": 0,
                        "buffer_minutes": 0,
                        "total_minutes": 120,
                        "status": "complete",
                    },
                    "description": "这是一个足够长的每日行程说明。",
                }
            ],
            "evaluation": {"passed": True, "issues": []},
            "notices": [],
        },
    }
    result = TravelPlanResponse.model_validate(body)
    assert validate_plan_shape(
        result,
        RepresentativeCity("四川省", "成都市"),
        start_date,
    ) == []

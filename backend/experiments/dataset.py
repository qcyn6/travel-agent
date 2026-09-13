import re

from backend.models.experiment import ExperimentCase


def _parse_interests(value: str) -> list[str]:
    soft_preferences = {"轻松", "节奏轻松"}
    return [
        item.strip()
        for item in re.split(r"[、，,]", value)
        if item.strip() and item.strip() not in soft_preferences
    ]


_SINGLE_DAY = [
    ("杭州", "历史文化、摄影"),
    ("绍兴", "古城、人文"),
    ("宁波", "海滨、历史"),
    ("昆明", "自然、摄影"),
    ("大理", "古城、自然"),
    ("丽江", "古城、摄影"),
    ("西安", "历史文化、美食"),
    ("宝鸡", "历史文化、自然"),
]

_SINGLE_CITY_MULTI_DAY = [
    ("杭州", 3, "自然、人文，节奏轻松"),
    ("绍兴", 2, "历史文化、古镇"),
    ("宁波", 3, "海滨、美食"),
    ("昆明", 3, "自然、摄影"),
    ("大理", 4, "古城、自然、骑行"),
    ("丽江", 3, "古城、摄影，节奏轻松"),
    ("西安", 4, "历史文化、美食"),
    ("汉中", 3, "自然、历史"),
]

_PROVINCE_MULTI_CITY = [
    ("浙江", 5, "自然、人文、古镇"),
    ("浙江", 7, "历史文化、摄影、美食"),
    ("云南", 5, "自然、古城"),
    ("云南", 7, "自然、古城、摄影"),
    ("云南", 8, "摄影、美食、轻松"),
    ("陕西", 5, "历史文化、美食"),
    ("陕西", 7, "历史、自然、摄影"),
    ("陕西", 8, "人文、自然、节奏轻松"),
]


EXPERIMENT_CASES = [
    *[
        ExperimentCase(
            id=f"SD-{index:02d}",
            group="single_day",
            text=f"去{city}玩一天，喜欢{interests}",
            travel_scope="single_city",
            days=1,
            interests=_parse_interests(interests),
        )
        for index, (city, interests) in enumerate(_SINGLE_DAY, start=1)
    ],
    *[
        ExperimentCase(
            id=f"MD-{index:02d}",
            group="single_city_multi_day",
            text=f"去{city}玩{days}天，喜欢{preference}",
            travel_scope="single_city",
            days=days,
            interests=_parse_interests(preference),
        )
        for index, (city, days, preference) in enumerate(_SINGLE_CITY_MULTI_DAY, start=1)
    ],
    *[
        ExperimentCase(
            id=f"PM-{index:02d}",
            group="province_multi_city",
            text=f"去{province}玩{days}天，想去两三个城市，喜欢{preference}",
            travel_scope="multi_city",
            days=days,
            interests=_parse_interests(preference),
        )
        for index, (province, days, preference) in enumerate(_PROVINCE_MULTI_CITY, start=1)
    ],
]

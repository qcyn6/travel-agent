from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.tables import CityRow, ProvinceRow


CATALOG: tuple[dict, ...] = (
    {
        "name": "浙江省",
        "adcode": "330000",
        "tags": ["江南", "历史文化", "自然", "古镇"],
        "description": "兼具江南人文、山水与海滨体验。",
        "cities": (
            ("杭州市", "330100", ["自然", "历史文化", "摄影", "休闲"], "西湖山水与宋韵文化名城。", 1, 4),
            ("绍兴市", "330600", ["历史文化", "古镇", "人文"], "水乡、黄酒与名人文化名城。", 1, 3),
            ("宁波市", "330200", ["海滨", "历史文化", "美食"], "港城文化与山海体验兼备。", 1, 3),
        ),
    },
    {
        "name": "云南省",
        "adcode": "530000",
        "tags": ["自然", "古城", "摄影", "民族文化"],
        "description": "以高原风光、古城和多民族文化见长。",
        "cities": (
            ("昆明市", "530100", ["自然", "人文", "美食"], "四季宜游的高原春城。", 1, 3),
            ("大理市", "532901", ["自然", "古城", "摄影", "休闲"], "苍山洱海与古城慢生活目的地。", 2, 4),
            ("丽江市", "530700", ["自然", "古城", "摄影", "人文"], "雪山、古城与纳西文化目的地。", 2, 4),
        ),
    },
    {
        "name": "陕西省",
        "adcode": "610000",
        "tags": ["历史文化", "人文", "自然"],
        "description": "中华文明重要发祥地之一。",
        "cities": (
            ("西安市", "610100", ["历史文化", "人文", "美食"], "古都遗址与城市文化体验丰富。", 2, 5),
            ("宝鸡市", "610300", ["历史文化", "自然"], "周秦文化与秦岭风光目的地。", 1, 3),
            ("延安市", "610600", ["历史文化", "人文", "自然"], "红色文化与黄土高原风貌目的地。", 1, 3),
        ),
    },
)


def seed_catalog(session: Session) -> None:
    if session.scalar(select(ProvinceRow.id).limit(1)) is not None:
        return

    for item in CATALOG:
        province = ProvinceRow(
            name=item["name"],
            adcode=item["adcode"],
            tags=item["tags"],
            description=item["description"],
        )
        session.add(province)
        session.flush()
        for name, adcode, tags, description, min_days, max_days in item["cities"]:
            session.add(
                CityRow(
                    province_id=province.id,
                    name=name,
                    adcode=adcode,
                    tags=tags,
                    description=description,
                    suitable_days_min=min_days,
                    suitable_days_max=max_days,
                )
            )
    session.commit()

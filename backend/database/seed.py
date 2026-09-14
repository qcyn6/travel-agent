from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.catalog import CATALOG
from backend.database.tables import CityRow, ProvinceRow


def seed_catalog(session: Session) -> None:
    """Insert or refresh the nationwide destination catalog without duplicating rows."""

    provinces_by_adcode = {
        item.adcode: item for item in session.scalars(select(ProvinceRow))
    }
    provinces_by_name = {
        item.name: item for item in provinces_by_adcode.values()
    }
    cities_by_adcode = {
        item.adcode: item for item in session.scalars(select(CityRow))
    }
    cities_by_name = {
        item.name: item for item in cities_by_adcode.values()
    }

    for item in CATALOG:
        province = provinces_by_adcode.get(item["adcode"]) or provinces_by_name.get(item["name"])
        if province is None:
            province = ProvinceRow(
                name=item["name"],
                adcode=item["adcode"],
                tags=item["tags"],
                description=item["description"],
            )
            session.add(province)
            session.flush()
            provinces_by_adcode[province.adcode] = province
            provinces_by_name[province.name] = province
        else:
            province.name = item["name"]
            province.adcode = item["adcode"]
            province.tags = item["tags"]
            province.description = item["description"]

        for name, adcode, tags, description, min_days, max_days in item["cities"]:
            city = cities_by_adcode.get(adcode) or cities_by_name.get(name)
            if city is None:
                city = CityRow(
                    province_id=province.id,
                    name=name,
                    adcode=adcode,
                    tags=tags,
                    description=description,
                    suitable_days_min=min_days,
                    suitable_days_max=max_days,
                )
                session.add(city)
                session.flush()
                cities_by_adcode[city.adcode] = city
                cities_by_name[city.name] = city
            else:
                city.province_id = province.id
                city.name = name
                city.adcode = adcode
                city.tags = tags
                city.description = description
                city.suitable_days_min = min_days
                city.suitable_days_max = max_days

    session.commit()


__all__ = ["CATALOG", "seed_catalog"]

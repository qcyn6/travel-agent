from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.catalog import CITY_ALIASES, PROVINCE_ALIASES
from backend.database import get_db
from backend.database.tables import CityRow, ProvinceRow
from backend.tag_matching import normalize_interest_tags
from backend.tag_vocabulary import canonicalize_tags


def canonical_province_name(name: str) -> str:
    value = name.strip()
    if value in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[value]
    return value if value.endswith(("省", "市", "自治区")) else f"{value}省"


def canonical_city_name(name: str) -> str:
    value = name.strip()
    if value in CITY_ALIASES:
        return CITY_ALIASES[value][0]
    # “杭州/兰州”中的“州”是名称的一部分，不能据此判断行政区后缀。
    return value if value.endswith(("市", "自治州", "地区", "盟", "林区", "县", "区")) else f"{value}市"


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_province(self, name: str) -> ProvinceRow | None:
        canonical = canonical_province_name(name)
        return self.session.scalar(select(ProvinceRow).where(ProvinceRow.name == canonical))

    def list_provinces(self) -> list[ProvinceRow]:
        return list(self.session.scalars(select(ProvinceRow).order_by(ProvinceRow.id)))

    def list_interest_tags(self) -> list[str]:
        """Return the de-duplicated interest vocabulary stored in the DB.

        Destination-level interests are allowed to use tags from either the
        province row or the city row.  The database remains the source of
        truth; the seed catalogue is not consulted here.
        """

        values: list[str] = []
        for tags in self.session.scalars(select(ProvinceRow.tags)):
            values.extend(tags or [])
        for tags in self.session.scalars(select(CityRow.tags)):
            values.extend(tags or [])
        result: list[str] = []
        for tag in canonicalize_tags(values):
            if tag not in result:
                result.append(tag)
        return result

    def normalize_interest_tags(
        self,
        interests: list[str] | None,
    ) -> tuple[list[str], list[str]]:
        return normalize_interest_tags(interests, self.list_interest_tags())

    def find_city(self, name: str) -> CityRow | None:
        canonical = canonical_city_name(name)
        return self.session.scalar(select(CityRow).where(CityRow.name == canonical))

    def list_cities_by_province(self, province_name: str) -> list[CityRow]:
        canonical = canonical_province_name(province_name)
        return list(
            self.session.scalars(
                select(CityRow)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == canonical)
                .order_by(CityRow.id)
            )
        )


def get_catalog_repository(session: Session = Depends(get_db)) -> CatalogRepository:
    return CatalogRepository(session)

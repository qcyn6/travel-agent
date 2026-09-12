from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.database.tables import CityRow, ProvinceRow


def canonical_province_name(name: str) -> str:
    value = name.strip()
    return value if value.endswith(("省", "市", "自治区")) else f"{value}省"


def canonical_city_name(name: str) -> str:
    value = name.strip()
    # “杭州/兰州”中的“州”是名称的一部分，不能据此判断行政区后缀。
    return value if value.endswith(("市", "自治州", "地区", "盟")) else f"{value}市"


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_province(self, name: str) -> ProvinceRow | None:
        canonical = canonical_province_name(name)
        return self.session.scalar(select(ProvinceRow).where(ProvinceRow.name == canonical))

    def list_provinces(self) -> list[ProvinceRow]:
        return list(self.session.scalars(select(ProvinceRow).order_by(ProvinceRow.id)))

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

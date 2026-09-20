from types import SimpleNamespace

import pytest

from backend.database.seed import CATALOG
from backend.destination.repository import canonical_city_name, canonical_province_name


@pytest.fixture
def catalog_repository():
    provinces = {}
    cities = {}
    for province_id, item in enumerate(CATALOG, start=1):
        province = SimpleNamespace(
            id=province_id,
            name=item["name"],
            adcode=item["adcode"],
            tags=item["tags"],
            description=item["description"],
        )
        provinces[province.name] = province
        for city_id, city_data in enumerate(item["cities"], start=len(cities) + 1):
            name, adcode, tags, description = city_data
            cities[name] = SimpleNamespace(
                id=city_id,
                province_id=province_id,
                province=province,
                name=name,
                adcode=adcode,
                tags=tags,
                description=description,
            )

    class FakeCatalogRepository:
        def find_province(self, name):
            return provinces.get(canonical_province_name(name))

        def list_provinces(self):
            return list(provinces.values())

        def find_city(self, name):
            return cities.get(canonical_city_name(name))

        def list_cities_by_province(self, province_name):
            province = provinces.get(canonical_province_name(province_name))
            if province is None:
                return []
            return [city for city in cities.values() if city.province_id == province.id]

    return FakeCatalogRepository()

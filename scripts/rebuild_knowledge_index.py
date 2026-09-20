"""Synchronize all described local attractions into Chroma."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.knowledge.repository import KnowledgeRepository  # noqa: E402
from backend.rag.runtime import build_vector_service  # noqa: E402


async def rebuild(*, city_names: list[str], limit: int | None) -> tuple[int, int, int]:
    settings = get_settings()
    with open_session() as session:
        repository = KnowledgeRepository(session)
        service = build_vector_service(repository, settings)
        indexed = 0
        skipped = 0
        failed = 0
        selected = city_names if limit is None else city_names[:limit]
        for index, city in enumerate(selected, start=1):
            try:
                count = await service.index_city(city)
            except Exception as exc:  # noqa: BLE001 - continue with other cities
                failed += 1
                print(f"failed {index}/{len(selected)} | {city} | {type(exc).__name__}: {exc}", flush=True)
                continue
            if count:
                indexed += count
                print(f"indexed {index}/{len(selected)} | {city} | {count}", flush=True)
            else:
                skipped += 1
        return indexed, skipped, failed


def ready_city_names() -> list[str]:
    settings = get_settings()
    with open_session() as session:
        statement = (
            select(CityRow.name)
            .join(AttractionRow, AttractionRow.city_id == CityRow.id)
            .where(
                AttractionRow.description.is_not(None),
                AttractionRow.description != "",
            )
            .distinct()
            .order_by(CityRow.name)
        )
        return list(session.scalars(statement))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理多少个城市，默认 0 表示全部",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit 不能小于 0")
    limit = None if args.limit == 0 else args.limit
    cities = ready_city_names()
    selected_count = len(cities) if limit is None else min(limit, len(cities))
    print(f"开始同步 {selected_count}/{len(cities)} 个城市的 ready 知识", flush=True)
    indexed, skipped, failed = asyncio.run(rebuild(city_names=cities, limit=limit))
    print(
        f"索引同步完成：indexed={indexed}, skipped_cities={skipped}, failed_cities={failed}",
        flush=True,
    )


if __name__ == "__main__":
    main()

"""为云南数据库中缺少向量的景点补建 Chroma 向量。

只处理当前数据库属于云南省、且在景点集合中没有向量的记录；不会调用高德，
不会改写 MySQL，也不会重建已经存在的向量。嵌入服务失败时不会写入半批结果。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.knowledge.repository import KnowledgeRepository  # noqa: E402
from backend.rag.embedding import OpenAICompatibleEmbeddingClient  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


YUNNAN = "云南省"


def _load_missing():
    init_database()
    settings = get_settings()
    with open_session() as session:
        repository = KnowledgeRepository(session)
        city_names = list(
            session.scalars(
                select(CityRow.name)
                .join(AttractionRow, AttractionRow.city_id == CityRow.id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(
                    ProvinceRow.name == YUNNAN,
                    AttractionRow.description.is_not(None),
                    AttractionRow.description != "",
                )
                .distinct()
                .order_by(CityRow.name)
            )
        )
        documents = [
            document
            for city in city_names
            for document in repository.list_documents(city)
        ]
    index = ChromaAttractionIndex(
        settings.chroma_path,
        collection_name=settings.chroma_collection,
    )
    existing_ids = set(index.collection.get(include=[]).get("ids", []))
    missing = [document for document in documents if document.poi_id not in existing_ids]
    return settings, index, missing


async def rebuild() -> dict:
    settings, index, missing = _load_missing()
    if not missing:
        return {
            "province": YUNNAN,
            "missing_before": 0,
            "embedded": 0,
            "message": "云南景点向量已经齐全。",
        }
    client = OpenAICompatibleEmbeddingClient(
        settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=10,
    )
    embeddings = await client.embed([document.index_text() for document in missing])
    indexed = index.upsert(missing, embeddings)
    stored = set(index.collection.get(ids=[document.poi_id for document in missing], include=[]).get("ids", []))
    return {
        "province": YUNNAN,
        "missing_before": len(missing),
        "embedded": indexed,
        "verified_present_after": len(stored),
        "poi_ids": [document.poi_id for document in missing],
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "message": "已补建云南缺失景点向量；未调用高德。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="执行嵌入请求并写入本地向量库")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.commit:
        _settings, _index, missing = _load_missing()
        print(json.dumps({"province": YUNNAN, "mode": "preview", "missing": len(missing), "poi_ids": [document.poi_id for document in missing]}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(asyncio.run(rebuild()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

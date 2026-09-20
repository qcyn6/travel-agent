"""补建或刷新云南数据库对应的 Chroma 向量。

只处理当前数据库属于云南省、且没有向量或向量内容哈希已经过期的记录；
不会调用高德，也不会改写 MySQL。嵌入服务失败时不会写入半批结果。
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
from backend.rag.vector_store import (  # noqa: E402
    ChromaAttractionIndex,
    _content_hash,
)


YUNNAN = "云南省"


def _load_needs_rebuild():
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
    current_ids = [document.poi_id for document in documents]
    stored = index.collection.get(ids=current_ids, include=["metadatas"])
    metadata_by_id = {
        poi_id: metadata
        for poi_id, metadata in zip(
            stored.get("ids", []),
            stored.get("metadatas", []),
            strict=False,
        )
        if isinstance(metadata, dict)
    }
    needs_rebuild = [
        document
        for document in documents
        if (
            document.poi_id not in metadata_by_id
            or metadata_by_id[document.poi_id].get("content_hash") != _content_hash(document)
            or metadata_by_id[document.poi_id].get("scope") != "all"
        )
    ]
    missing = [
        document for document in needs_rebuild if document.poi_id not in metadata_by_id
    ]
    stale = [
        document for document in needs_rebuild if document.poi_id in metadata_by_id
    ]
    return settings, index, needs_rebuild, missing, stale


async def rebuild() -> dict:
    settings, index, needs_rebuild, missing, stale = _load_needs_rebuild()
    if not needs_rebuild:
        return {
            "province": YUNNAN,
            "missing_before": 0,
            "stale_before": 0,
            "embedded": 0,
            "message": "云南景点向量已经齐全且与数据库内容一致。",
        }
    client = OpenAICompatibleEmbeddingClient(
        settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=10,
    )
    embeddings = await client.embed([document.index_text() for document in needs_rebuild])
    indexed = index.upsert(needs_rebuild, embeddings)
    stored = set(index.collection.get(ids=[document.poi_id for document in needs_rebuild], include=[]).get("ids", []))
    return {
        "province": YUNNAN,
        "missing_before": len(missing),
        "stale_before": len(stale),
        "embedded": indexed,
        "verified_present_after": len(stored),
        "poi_ids": [document.poi_id for document in needs_rebuild],
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "message": "已补建或刷新云南景点向量；未调用高德。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="执行嵌入请求并写入本地向量库")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.commit:
        _settings, _index, needs_rebuild, missing, stale = _load_needs_rebuild()
        print(json.dumps({"province": YUNNAN, "mode": "preview", "missing": len(missing), "stale": len(stale), "rebuild": len(needs_rebuild), "poi_ids": [document.poi_id for document in needs_rebuild]}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(asyncio.run(rebuild()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

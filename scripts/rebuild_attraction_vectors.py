"""Rebuild all stale/missing attraction vectors from the current MySQL table.

This is a local Chroma synchronization helper.  It only uses the configured
embedding service; it never calls Amap and it does not change the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.knowledge.repository import KnowledgeRepository  # noqa: E402
from backend.rag.embedding import OpenAICompatibleEmbeddingClient  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex, _content_hash  # noqa: E402


def _load():
    init_database()
    settings = get_settings()
    with open_session() as session:
        city_names = list(
            session.scalars(
                select(CityRow.name)
                .join(AttractionRow, AttractionRow.city_id == CityRow.id)
                .where(AttractionRow.description.is_not(None), AttractionRow.description != "")
                .distinct()
                .order_by(CityRow.name)
            )
        )
        repository = KnowledgeRepository(session)
        documents = [document for city in city_names for document in repository.list_documents(city)]
    index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
    current_ids = [document.poi_id for document in documents]
    stored = index.collection.get(ids=current_ids, include=["metadatas"])
    metadata_by_id = {
        poi_id: metadata
        for poi_id, metadata in zip(stored.get("ids", []), stored.get("metadatas", []), strict=False)
        if isinstance(metadata, dict)
    }
    needs = [
        document
        for document in documents
        if document.poi_id not in metadata_by_id
        or metadata_by_id[document.poi_id].get("content_hash") != _content_hash(document)
        or metadata_by_id[document.poi_id].get("scope") != "all"
    ]
    missing = [document for document in needs if document.poi_id not in metadata_by_id]
    stale = [document for document in needs if document.poi_id in metadata_by_id]
    return settings, index, needs, missing, stale


async def rebuild() -> dict:
    settings, index, needs, missing, stale = _load()
    if not needs:
        return {"missing_before": 0, "stale_before": 0, "embedded": 0, "message": "全部景点向量已与数据库一致。"}
    # Keep TLS verification enabled. For a trusted local proxy, configure its
    # CA bundle through SSL_CERT_FILE instead of disabling verification.
    async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as http_client:
        client = OpenAICompatibleEmbeddingClient(
            settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.embedding_timeout_seconds,
            batch_size=10,
            client=http_client,
        )
        indexed = 0
        # Upsert each small batch immediately.  A transient provider failure
        # then cannot discard already completed batches.
        batch_size = 10
        for start in range(0, len(needs), batch_size):
            batch_documents = needs[start : start + batch_size]
            texts = [document.index_text() for document in batch_documents]
            embeddings = None
            last_error: Exception | None = None
            for attempt in range(1, 9):
                try:
                    embeddings = await client.embed(texts)
                    break
                except Exception as exc:  # transient provider/network errors are common
                    last_error = exc
                    if attempt == 8:
                        raise
                    await asyncio.sleep(min(45, 2**attempt))
            if embeddings is None:
                raise last_error or RuntimeError("嵌入服务未返回向量")
            indexed += index.upsert(batch_documents, embeddings)
    stored = set(index.collection.get(ids=[document.poi_id for document in needs], include=[]).get("ids", []))
    return {
        "missing_before": len(missing),
        "stale_before": len(stale),
        "embedded": indexed,
        "verified_present_after": len(stored),
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "message": "已刷新本地景点向量；未调用高德。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="实际调用嵌入服务并写入 Chroma")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.commit:
        _settings, _index, needs, missing, stale = _load()
        print(json.dumps({"mode": "preview", "missing": len(missing), "stale": len(stale), "rebuild": len(needs)}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(asyncio.run(rebuild()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

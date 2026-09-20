"""Rebuild the local Chroma attraction index with bounded concurrency.

This command only vectorizes the database-owned attraction descriptions.  It
does not discover, fetch, or search for attraction data.  Embedding requests
are limited to small batches and a small number of concurrent requests so a
temporary provider failure does not damage the existing index.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.knowledge.repository import KnowledgeRepository  # noqa: E402
from backend.rag.runtime import build_vector_service  # noqa: E402


async def _embed_batch(embedding_client, documents, semaphore, retries):
    texts = [document.index_text() for document in documents]
    async with semaphore:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await embedding_client.embed(texts)
            except Exception as exc:  # noqa: BLE001 - retry one embedding batch
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last_error or RuntimeError("embedding batch failed")


async def rebuild(
    *,
    concurrency: int,
    retries: int,
    batch_size: int,
    missing_only: bool = False,
) -> dict:
    settings = get_settings()
    with open_session() as session:
        repository = KnowledgeRepository(session)
        cities = list(
            session.scalars(
                select(CityRow.name)
                .join(AttractionRow, AttractionRow.city_id == CityRow.id)
                .where(
                    AttractionRow.description.is_not(None),
                    AttractionRow.description != "",
                )
                .distinct()
                .order_by(CityRow.name)
            )
        )
        documents = [
            document
            for city in cities
            for document in repository.list_documents(city)
        ]
        service = build_vector_service(repository, settings)
        embedding_client = service.embedding_client
        vector_index = service.vector_index
        existing_ids = set(vector_index.collection.get(include=[]).get("ids", []))
        all_documents = documents
        if missing_only:
            documents = [
                document for document in documents if document.poi_id not in existing_ids
            ]
        batches = [documents[start : start + batch_size] for start in range(0, len(documents), batch_size)]

        async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
            embedding_client.client = client
            semaphore = asyncio.Semaphore(max(1, concurrency))
            tasks = [
                asyncio.create_task(
                    _embed_batch(embedding_client, batch, semaphore, retries)
                )
                for batch in batches
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            vectors: list[list[float]] = []
            failed_batches = 0
            for index, result in enumerate(results, start=1):
                if isinstance(result, Exception):
                    failed_batches += 1
                    print(
                        f"failed batch {index}/{len(tasks)} | {type(result).__name__}: {result}",
                        flush=True,
                    )
                else:
                    vectors.extend(result)
                if index % 10 == 0 or index == len(tasks):
                    print(f"embedded batches {index}/{len(tasks)}", flush=True)

        if failed_batches:
            return {
                "documents": len(documents),
                "batches": len(batches),
                "embedded_vectors": len(vectors),
                "failed_batches": failed_batches,
                "upserted": 0,
            }

        if not missing_only:
            desired_ids = {document.poi_id for document in all_documents}
            stale_ids = existing_ids - desired_ids
            if stale_ids:
                vector_index.collection.delete(ids=list(stale_ids))
        for start in range(0, len(documents), batch_size):
            vector_index.upsert(
                documents[start : start + batch_size],
                vectors[start : start + batch_size],
            )
        return {
            "documents": len(documents),
            "batches": len(batches),
            "embedded_vectors": len(vectors),
            "failed_batches": 0,
            "upserted": len(vectors),
            "missing_only": missing_only,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="只向量化 Chroma 中不存在的 POI，不重算已有向量",
    )
    args = parser.parse_args()
    if args.concurrency < 1 or args.retries < 0 or not 1 <= args.batch_size <= 10:
        parser.error("concurrency 必须大于 0，retries 不能小于 0，batch-size 必须在 1 到 10 之间")
    result = asyncio.run(
        rebuild(
            concurrency=args.concurrency,
            retries=args.retries,
            batch_size=args.batch_size,
            missing_only=args.missing_only,
        )
    )
    print(result)


if __name__ == "__main__":
    main()

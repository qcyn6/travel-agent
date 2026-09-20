"""Synchronize Chroma text/metadata from MySQL without embedding API calls.

This is useful after a local description or quality update. Existing vector
embeddings are preserved; rows without a vector are reported for a later
embedding run when the application is allowed to use its configured provider.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow  # noqa: E402
from backend.knowledge.repository import KnowledgeRepository  # noqa: E402
from backend.rag.vector_store import (  # noqa: E402
    ChromaAttractionIndex,
    _metadata_for_document,
)


def sync() -> dict[str, int]:
    init_database()
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

    index = ChromaAttractionIndex(
        settings.chroma_path,
        collection_name=settings.chroma_collection,
    )
    current_ids = {document.poi_id for document in documents}
    existing = index.collection.get(
        ids=list(current_ids),
        # Chroma requires an embedding whenever ``documents`` is updated for
        # a collection that has no configured embedding function.  Reusing the
        # stored vectors keeps this operation local and avoids an embedding
        # provider call.
        include=["metadatas", "embeddings"],
    )
    existing_ids = set(existing.get("ids", []))
    stored_embeddings = existing.get("embeddings")
    if stored_embeddings is None:
        stored_embeddings = []
    existing_embeddings = {
        poi_id: embedding
        for poi_id, embedding in zip(
            existing.get("ids", []),
            stored_embeddings,
            strict=False,
        )
        if embedding is not None
    }
    update_documents = [
        document for document in documents if document.poi_id in existing_ids
    ]
    for start in range(0, len(update_documents), 500):
        batch = update_documents[start : start + 500]
        batch_ids = [document.poi_id for document in batch]
        # Older Chroma versions may omit embeddings from ``get`` even when
        # requested.  In that case, update metadata only; the vector text will
        # be refreshed on the next authorized embedding rebuild.
        batch_embeddings = [existing_embeddings.get(poi_id) for poi_id in batch_ids]
        kwargs = {
            "ids": batch_ids,
            "metadatas": [
                _metadata_for_document(document, core_only=False)
                for document in batch
            ],
        }
        if all(embedding is not None for embedding in batch_embeddings):
            kwargs["documents"] = [document.index_text() for document in batch]
            kwargs["embeddings"] = batch_embeddings
        index.collection.update(
            **kwargs,
        )
    return {
        "active_database_documents": len(documents),
        "vectors_present": len(existing_ids),
        "metadata_updated": len(update_documents),
        "vectors_missing": len(current_ids - existing_ids),
    }


if __name__ == "__main__":
    print(json.dumps(sync(), ensure_ascii=False, indent=2))

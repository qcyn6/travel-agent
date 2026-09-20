"""Remove redundant metadata keys from the persistent Chroma collection.

Chroma merges metadata on ``update`` and ``upsert``, so removing a key requires
building a verified replacement collection and swapping it into place. Vector
embeddings and indexed documents are copied as-is; no embedding API calls are
made.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from uuid import uuid4

import chromadb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.rag.vector_store import (  # noqa: E402
    REDUNDANT_VECTOR_METADATA_FIELDS,
)


DEFAULT_REDUNDANT_FIELDS = REDUNDANT_VECTOR_METADATA_FIELDS
COPY_BATCH_SIZE = 500


def _chunks(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def compact_collection(*, path: str, collection_name: str, fields: set[str]) -> dict:
    unknown_fields = fields - set(REDUNDANT_VECTOR_METADATA_FIELDS)
    if unknown_fields:
        raise ValueError(f"不认识的向量元数据字段: {sorted(unknown_fields)}")

    client = chromadb.PersistentClient(path=path)
    collection = client.get_collection(name=collection_name)
    snapshot = collection.get(include=["documents", "embeddings", "metadatas"])
    ids = list(snapshot.get("ids", []))
    metadatas = list(snapshot.get("metadatas", []))
    documents = list(snapshot.get("documents", []))
    embeddings = snapshot.get("embeddings")
    embedding_values = (
        embeddings.tolist()
        if hasattr(embeddings, "tolist")
        else list(embeddings or [])
    )

    if not ids:
        return {"collection": collection_name, "records": 0, "changed": 0}
    if len({len(metadatas), len(documents), len(embedding_values)}) != 1:
        raise RuntimeError("向量集合快照字段数量不一致，已停止迁移")

    cleaned_metadatas = [
        {key: value for key, value in (metadata or {}).items() if key not in fields}
        for metadata in metadatas
    ]
    changed = sum(
        old_metadata != new_metadata
        for old_metadata, new_metadata in zip(metadatas, cleaned_metadatas, strict=True)
    )
    if not changed:
        return {"collection": collection_name, "records": len(ids), "changed": 0}

    temporary_name = f"{collection_name}__compact_{uuid4().hex[:12]}"
    replacement = client.get_or_create_collection(
        name=temporary_name,
        metadata=collection.metadata or {"hnsw:space": "cosine"},
    )
    original_deleted = False
    try:
        for id_batch, document_batch, embedding_batch, metadata_batch in zip(
            _chunks(ids, COPY_BATCH_SIZE),
            _chunks(documents, COPY_BATCH_SIZE),
            _chunks(embedding_values, COPY_BATCH_SIZE),
            _chunks(cleaned_metadatas, COPY_BATCH_SIZE),
            strict=True,
        ):
            replacement.add(
                ids=id_batch,
                documents=document_batch,
                embeddings=embedding_batch,
                metadatas=metadata_batch,
            )

        verification = replacement.get(include=["documents", "embeddings", "metadatas"])
        verification_ids = list(verification.get("ids", []))
        verification_metadatas = list(verification.get("metadatas", []))
        if set(verification_ids) != set(ids) or len(verification_ids) != len(ids):
            raise RuntimeError("替换集合校验失败：记录数量或 ID 不一致")
        if any(field in (metadata or {}) for metadata in verification_metadatas for field in fields):
            raise RuntimeError("替换集合校验失败：冗余字段仍然存在")

        # Keep the original collection untouched until the replacement has
        # the same records and no requested redundant field.
        client.delete_collection(name=collection_name)
        original_deleted = True
        try:
            replacement.modify(name=collection_name)
        except Exception:
            # The replacement is intentionally left in place for manual
            # recovery if the final rename fails after the original is gone.
            raise RuntimeError(
                f"原集合已删除，但重命名失败；可从临时集合 {temporary_name} 恢复"
            ) from None
    except Exception:
        # Before the final swap, a failed migration only owns the temporary
        # collection and can be removed without touching the live collection.
        if not original_deleted:
            try:
                client.get_collection(name=temporary_name)
            except Exception:
                pass
            else:
                client.delete_collection(name=temporary_name)
        raise

    return {
        "collection": collection_name,
        "records": len(ids),
        "changed": changed,
        "removed_fields": sorted(fields),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fields",
        nargs="+",
        default=sorted(DEFAULT_REDUNDANT_FIELDS),
        help="要移除的每条向量元数据字段",
    )
    args = parser.parse_args()
    settings = get_settings()
    result = compact_collection(
        path=settings.chroma_path,
        collection_name=settings.chroma_collection,
        fields=set(args.fields),
    )
    print(result)


if __name__ == "__main__":
    main()

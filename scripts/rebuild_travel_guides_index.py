"""Build the independent travel-topic Chroma index."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.rag.embedding import EmbeddingError  # noqa: E402
from backend.rag.runtime import build_guide_retrieval_service  # noqa: E402


async def rebuild() -> int:
    settings = get_settings()
    service = build_guide_retrieval_service(settings)
    documents = service.source.list_documents()
    async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
        service.embedding_client.client = client
        indexed_count = 0
        for attempt in range(1, 4):
            try:
                indexed_count = await service.index_all()
                break
            except EmbeddingError:
                if attempt == 3:
                    raise
                print(f"Embedding 请求暂时失败，2 秒后重试（{attempt}/3）", flush=True)
                await asyncio.sleep(2)
    print(
        "旅游主题知识库索引完成："
        f"documents={len(documents)}, indexed={indexed_count}, "
        f"collection={settings.guide_knowledge_chroma_collection}",
        flush=True,
    )
    return indexed_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        asyncio.run(rebuild())
    except Exception as exc:  # noqa: BLE001 - provide a concise CLI failure
        print(f"旅游主题知识库索引失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

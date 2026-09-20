"""Remove vectors for inactive attraction rows from the derived Chroma index."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow  # noqa: E402
from backend.rag.runtime import build_vector_service  # noqa: E402


def prune() -> dict[str, int]:
    init_database()
    settings = get_settings()
    with open_session() as session:
        active_ids = set(
            session.scalars(
                select(AttractionRow.poi_id).where(AttractionRow.is_active.is_(True))
            )
        )
    # The repository is not used for embedding here; a lightweight object keeps
    # this maintenance command independent of network credentials.
    service = build_vector_service(None, settings)
    collection = service.vector_index.collection
    existing_ids = set(collection.get(include=[]).get("ids", []))
    stale_ids = sorted(existing_ids - active_ids)
    for start in range(0, len(stale_ids), 500):
        collection.delete(ids=stale_ids[start : start + 500])
    return {
        "active_database_rows": len(active_ids),
        "vectors_before": len(existing_ids),
        "vectors_removed": len(stale_ids),
        "vectors_after": len(existing_ids) - len(stale_ids),
    }


if __name__ == "__main__":
    print(json.dumps(prune(), ensure_ascii=False, indent=2))

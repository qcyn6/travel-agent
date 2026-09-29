"""Compress Yunnan attraction tags into a smaller matching vocabulary.

Only ``tags`` and the tag phrase in generated catalog descriptions are
updated.  The attraction schema and every other province remain unchanged.
Preview is the default; ``--commit`` writes a JSON backup before mutation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.tag_vocabulary import COMPACT_TAG_MAPPING, is_canonical_tag  # noqa: E402


TAG_MAPPING = COMPACT_TAG_MAPPING

_TAG_PHRASE = re.compile(r"现有兴趣标签为[^。]+。")


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-before-tag-compression-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-before-tag-compression-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _row_payload(row: AttractionRow) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _compress(tags: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in tags or []:
        tag = str(raw).strip()
        if not tag:
            continue
        if tag not in TAG_MAPPING:
            raise RuntimeError(f"云南数据出现未配置的标签：{tag}")
        for mapped in TAG_MAPPING[tag]:
            if mapped not in result:
                result.append(mapped)
    if not result:
        raise RuntimeError("压缩后出现空标签")
    return result


def _refresh_generated_description(description: str | None, tags: list[str]) -> str | None:
    text = description or ""
    if not _TAG_PHRASE.search(text):
        return description
    return _TAG_PHRASE.sub(f"现有兴趣标签为{'、'.join(tags[:6])}。", text, count=1)


def _counts(rows: list[AttractionRow], attr: str = "tags") -> Counter[str]:
    return Counter(tag for row in rows for tag in (getattr(row, attr) or []))


def compress(*, commit: bool, backup_dir: Path, sample_limit: int) -> dict:
    init_database()
    with open_session() as session:
        province = session.scalar(select(ProvinceRow).where(ProvinceRow.name == "云南省"))
        if province is None:
            raise RuntimeError("数据库中没有找到云南省")
        rows = list(
            session.scalars(
                select(AttractionRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .where(CityRow.province_id == province.id)
                .order_by(AttractionRow.id)
            )
        )
        before = _counts(rows)
        unknown = sorted(set(before).difference(TAG_MAPPING))
        if unknown:
            raise RuntimeError(f"云南数据出现未配置的标签：{unknown}")

        updates = []
        after = Counter()
        for row in rows:
            tags = _compress(row.tags)
            after.update(tags)
            description = _refresh_generated_description(row.description, tags)
            if tags != list(row.tags or []) or description != row.description:
                updates.append(
                    {
                        "row": row,
                        "poi_id": row.poi_id,
                        "name": row.name,
                        "before": list(row.tags or []),
                        "after": tags,
                        "description": description,
                    }
                )

        invalid = sorted(tag for tag in after if not is_canonical_tag(tag))
        if invalid:
            raise RuntimeError(f"压缩结果含非标准标签：{invalid}")

        backup = None
        if commit and updates:
            backup = _backup_path(backup_dir)
            backup.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "province": "云南省",
                        "rows": [_row_payload(row) for row in rows],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            for item in updates:
                item["row"].tags = item["after"]
                item["row"].description = item["description"]
            session.commit()

        return {
            "province": "云南省",
            "attractions": len(rows),
            "changed_rows": len(updates),
            "committed": commit,
            "backup_path": str(backup) if backup else None,
            "before": {
                "tag_types": len(before),
                "assignments": sum(before.values()),
                "average_per_attraction": round(sum(before.values()) / len(rows), 3),
                "counts": dict(before.most_common()),
            },
            "after": {
                "tag_types": len(after),
                "assignments": sum(after.values()),
                "average_per_attraction": round(sum(after.values()) / len(rows), 3),
                "counts": dict(after.most_common()),
            },
            "sample": [
                {key: item[key] for key in ("poi_id", "name", "before", "after")}
                for item in updates[:sample_limit]
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="正式写入 MySQL")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("sample-limit 不能小于 0")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        json.dumps(
            compress(commit=args.commit, backup_dir=args.backup_dir, sample_limit=args.sample_limit),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

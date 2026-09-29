"""删除全国审计中确认的高置信度父子/后缀重复景点。

景区与“景区公园/景区”后缀记录指向同一个游览实体时，只保留一个记录，避免
检索和日程编排重复命中。对“洱海公园/洱海”等确实可能是不同游览实体的组合
不处理。默认预览，``--commit`` 才写入；不调用高德、不新增字段。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


# 被删记录 -> 保留记录。保留 verified/curated 记录；没有 curated 记录的组，
# 保留名称更明确且不带重复“景区”后缀的一条。
TARGET = {
    "B000A7IKWK": "mct-4a-299",                    # 世界公园
    "B000A16E89": "mct-5a-34787",                  # 圆明园遗址公园
    "B021305SHC": "B0LRFH675Y",                    # 趵突泉景区 / 趵突泉
    "B015F0IS5A": "mct-4a-346",                   # 太原市森林公园 / 景区
    "B0KUCZUPFU": "B015F02AH1",                    # 晋祠公园 / 晋祠景区
    "B0FFIHK23Q": "mct-4a-775",                   # 南宁园博园
    "B030302QCX": "mct-4a-950",                   # 南宁市人民公园
    "B001905N9D": "mct-4a-641",                   # 南京大屠杀纪念馆
    "B031704VYJ": "mct-4a-1608",                  # 南昌八一起义纪念馆
    "B013901QY8": "B0FFHNB8BX",                   # 大境门景区 / 大境门
    "B023B025FB": "mct-4a-3299",                  # 杭州宋城
    "B0FFFVGKJH": "verified-hn-wuzhishan-yahu-terraces",
    "B07650045V": "verified-hn-wenchang-dongjiao-coconut",
    "B0H39HRZCP": "verified-hb-tianmen-luyu-park",
    "B001B0IY32": "B001B0I4K0",                   # 黄鹤楼公园 / 黄鹤楼
    "B0FFM3WKUK": "mct-5a-34994",                 # 花明楼 / 景区
    "B0H6PNJPIW": "verified-xz-naqu-biru-skeleton-wall",
    "B0JD2HMJ43": "verified-xz-ali-kailash",
    "B0381003OW": "verified-xz-ali-pangong-lake",
    "B01A505NOM": "mct-4a-25f6b2807007e04898ed249c1da3748d",
    "B0FFHUDX8R": "mct-4a-948b204c242073202434789df086d610",
    "B001707JK6": "mct-5a-35044",                 # 白帝城·瞿塘峡
    "B01C30JKAT": "mct-4a-1132",                  # 哈尔滨北方森林动物园
}


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    all_ids = set(TARGET) | set(TARGET.values())
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .select_from(AttractionRow)
                .join(CityRow, AttractionRow.city_id == CityRow.id)
                .join(ProvinceRow, CityRow.province_id == ProvinceRow.id)
                .where(AttractionRow.poi_id.in_(tuple(all_ids)))
            ).all()
        )
        found = {row.poi_id: (row, city.name, province.name) for row, city, province in pairs}
        missing = sorted(all_ids - set(found))
        if missing:
            raise RuntimeError(json.dumps({"missing": missing}, ensure_ascii=False))
        affected = []
        for remove_id, keep_id in TARGET.items():
            removed, remove_city, remove_province = found[remove_id]
            kept, keep_city, keep_province = found[keep_id]
            if (remove_city, remove_province) != (keep_city, keep_province):
                raise RuntimeError(f"父子重复记录不在同一城市：{remove_id} / {keep_id}")
            affected.append({"poi_id": remove_id, "province": remove_province, "city": remove_city, "name": removed.name, "kept_poi_id": keep_id, "kept_name": kept.name})
        rows = [found[poi_id][0] for poi_id in sorted(TARGET)]
        if not commit:
            return {"mode": "preview", "deletions": len(rows), "affected": affected, "message": "仅预览；传入 --commit 才删除。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"parent-child-duplicates-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "rows": [_payload(row) for row in rows], "affected": affected}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for row in rows:
            session.delete(row)
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=sorted(TARGET))
        vectors_deleted = len(TARGET)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"mode": "commit", "deletions": len(rows), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "已删除高置信度父子/后缀重复记录；未调用高德、未新增字段。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

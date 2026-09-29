"""核验河北张家口演示景点；默认预览，``--commit`` 才写库。"""

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


PROVINCE = "河北省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-130700-02": item(
        city="张家口市",
        name="崇礼滑雪旅游度假区",
        new_poi_id="verified-hb-zhangjiakou-chongli-ski-resort",
        category="休闲度假",
        tags=["休闲", "运动", "自然", "摄影"],
        duration=300,
        description="崇礼滑雪旅游度假区位于张家口市崇礼区，集多个滑雪场、山地户外和四季休闲活动于一体，是张家口冬奥和山地旅游的重要目的地。不同雪场和季节的开放项目差异较大，具体安排以运营方公告为准。",
        source_urls=[
            "https://whly.hebei.gov.cn/c/2022-09-29/569022.html",
            "https://sport.hebei.gov.cn/m/view.php?aid=31265",
            "https://zhuanti.mct.gov.cn/mmyghcyt2023/hjgn/detail/4967.html",
        ],
    ),
    "demo-130700-04": item(
        city="张家口市",
        name="鸡鸣山旅游景区",
        new_poi_id="verified-hb-zhangjiakou-jimingshan",
        category="自然山水",
        tags=["自然", "山地", "历史文化", "摄影"],
        duration=240,
        description="鸡鸣山旅游景区位于张家口市下花园区东南部，是以孤峰山体、山地景观和历史寺庙建筑为特色的国家4A级景区，适合登山、观景和人文摄影。登山强度和季节开放情况会影响实际游览时长。",
        source_urls=[
            "https://s.visitbeijing.com.cn/attraction/118688",
            "https://jimingshan.com/",
            "https://whly.hebei.gov.cn/c/2021-08-02/556540.html",
        ],
    ),
    "demo-130700-05": item(
        city="张家口市",
        name="黄帝城遗址文化旅游区",
        new_poi_id="verified-hb-zhangjiakou-huangdi-city",
        category="历史遗迹",
        tags=["历史文化", "人文", "研学", "古建"],
        duration=180,
        description="黄帝城遗址文化旅游区位于张家口市涿鹿县，以涿鹿故城（黄帝城）遗址、中华三祖堂、合符坛和黄帝泉等始祖文化遗存为核心，适合历史文化参观和研学。遗址保护、考古展示及景区开放范围以当地文旅和现场公告为准。",
        source_urls=[
            "https://whly.hebei.gov.cn/c/2025-06-15/582164.html",
            "https://mw.hebei.gov.cn/system/2023/07/19/030237272.shtml",
            "https://s.visitbeijing.com.cn/attraction/118905",
        ],
    ),
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"hebei-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"hebei-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(VERIFIED)
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids))
                .order_by(AttractionRow.poi_id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches = []
        aliases = {"demo-130700-04": {"鸡鸣山风景区", "鸡鸣山旅游景区"}}
        for poi_id, spec in VERIFIED.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name not in aliases.get(poi_id, {spec["name"]}):
                    mismatches.append({"poi_id": poi_id, "expected_name": spec["name"], "actual_name": row.name})
                if city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected_city": spec["city"], "actual_city": city})
        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = list(session.scalars(select(AttractionRow.poi_id).where(AttractionRow.poi_id.in_(new_ids))))
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))
        affected = [found[poi_id][0] for poi_id in target_ids]
        preview = [{"poi_id": row.poi_id, "city": found[row.poi_id][1], "old_name": row.name, "new_name": VERIFIED[row.poi_id]["name"], "action": "verify"} for row in affected]
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(affected), "affected": preview, "source_urls": sorted({url for spec in VERIFIED.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-hebei-zhangjiakou-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["name"]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        session.commit()
    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=list(target_ids))
        vectors_deleted = len(target_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(affected), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "河北张家口公开网页核验记录已写入；未调用高德，未新增数据库字段。"}


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

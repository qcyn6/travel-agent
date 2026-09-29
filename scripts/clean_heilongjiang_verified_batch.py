"""核验黑龙江七台河演示景点；默认预览，``--commit`` 才写库。"""

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


PROVINCE = "黑龙江省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-230900-01": item(
        city="七台河市",
        name="桃山公园",
        new_poi_id="verified-hlj-qitaihe-taoshan-park",
        category="城市公园",
        tags=["自然", "城市漫游", "休闲", "摄影"],
        duration=90,
        description="桃山公园位于七台河市桃山区城区，是依托桃山湖的城市公共公园，适合短时散步、休闲和城市景观摄影。它属于城市公共空间，游览时应结合现场开放情况安排。",
        source_urls=[
            "https://qth.zwfw.hlj.gov.cn/fwqd/qt/art/2024/art_bb8ad152b5c04f2c9cb6001a101f7445.html",
            "https://www.hlj.gov.cn/hlj/c107858/202508/c00_31867779.shtml",
        ],
    ),
    "demo-230900-03": item(
        city="七台河市",
        name="桃山湖",
        new_poi_id="verified-hlj-qitaihe-taoshan-lake",
        category="滨水景观",
        tags=["自然", "滨水", "休闲", "摄影"],
        duration=120,
        description="桃山湖是七台河市城区北侧的湖泊与湿地生态空间，原为桃山水库，现已纳入桃山湖生态湿地治理和公共休闲建设。适合湖滨散步、观鸟和摄影；具体游览范围以当日开放的步道、湿地和公共设施为准。",
        source_urls=[
            "https://www.hlj.gov.cn/hlj/c107858/202609/c00_31977128.shtml",
            "https://www.hlj.gov.cn/hlj/c107858/202604/c00_31931057.shtml",
            "https://lstjsd.com/html/.mlts.html",
        ],
    ),
    "demo-230900-05": item(
        city="七台河市",
        name="七台河市博物馆",
        new_poi_id="verified-hlj-qitaihe-museum",
        category="博物馆",
        tags=["历史文化", "博物馆", "人文", "研学"],
        duration=120,
        description="七台河市博物馆是当地综合性地方博物馆，设有地方历史、自然资源和英模文化等展陈单元，适合了解七台河的城市发展、矿业记忆和地域文化。开放时间、预约和临时展览以馆方当日公告为准。",
        source_urls=[
            "https://wlt.hlj.gov.cn/wlt/c114254/202307/c00_31648855.shtml",
            "https://wlt.hlj.gov.cn/wlt/c114164/202408/c00_31760830.shtml",
            "https://wlt.hlj.gov.cn/wlt/c114253/202411/c00_31788346.shtml",
        ],
    ),
}


DELETE: dict[str, dict[str, Any]] = {
    "demo-230900-04": {
        "city": "七台河市",
        "name": "勃利青山国家森林公园",
        "reason": "公开资料中的青山国家森林公园位于双鸭山市；勃利国家森林公园是另一名称，现有名称与七台河城区坐标混淆，无法确认该条为真实可规划景点。",
        "source_urls": [
            "https://www.sysld.gov.cn/ld/103/202002/c07_150010.shtml",
            "https://www.mee.gov.cn/zcwj/gwywj/201811/t20181129_676510.shtml",
        ],
    }
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"heilongjiang-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"heilongjiang-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple([*VERIFIED, *DELETE])
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
        aliases = {"demo-230900-03": {"桃山湖生态景区", "桃山湖"}}
        for poi_id, spec in {**VERIFIED, **DELETE}.items():
            if poi_id in found:
                row, city = found[poi_id]
                allowed_names = aliases.get(poi_id, {spec["name"]})
                if row.name not in allowed_names:
                    mismatches.append({"poi_id": poi_id, "expected_name": sorted(allowed_names), "actual_name": row.name})
                if city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected_city": spec["city"], "actual_city": city})
        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = list(session.scalars(select(AttractionRow.poi_id).where(AttractionRow.poi_id.in_(new_ids))))
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))
        verified_rows = [found[poi_id][0] for poi_id in VERIFIED]
        deleted_rows = [found[poi_id][0] for poi_id in DELETE]
        preview = {
            "verified": [{"poi_id": row.poi_id, "city": found[row.poi_id][1], "old_name": row.name, "new_name": VERIFIED[row.poi_id]["name"], "action": "verify"} for row in verified_rows],
            "deleted": [{"poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "reason": DELETE[row.poi_id]["reason"], "action": "delete"} for row in deleted_rows],
        }
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(verified_rows), "deletions": len(deleted_rows), "affected": preview, "source_urls": sorted({url for spec in {**VERIFIED, **DELETE}.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-heilongjiang-qitaihe-batch", "rows": [_payload(row) for row in [*verified_rows, *deleted_rows]], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in {**VERIFIED, **DELETE}.items()}, "decisions": {poi_id: ("verify" if poi_id in VERIFIED else "delete") for poi_id in target_ids}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["name"]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        for row in deleted_rows:
            session.delete(row)
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
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(verified_rows), "deletions": len(deleted_rows), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "黑龙江七台河公开网页核验结果已写入；未调用高德，未新增数据库字段。"}


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

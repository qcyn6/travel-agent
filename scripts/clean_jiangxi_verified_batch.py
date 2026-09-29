"""核验江西新余演示景点；默认预览，``--commit`` 才写库。"""

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


PROVINCE = "江西省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-360500-01": item(city="新余市", name="仙女湖风景名胜区", new_poi_id="verified-jx-xinyu-fairy-lake", category="滨水景观", tags=["自然", "滨水", "休闲", "摄影"], duration=240, description="仙女湖风景名胜区位于新余市，以湖泊、岛屿和七夕文化旅游体验为主要特色，是国家级旅游度假区和国家重点风景名胜区，适合湖区观光、休闲与摄影。船班、天气和景区开放信息会影响具体游览安排。", source_urls=["https://fgw.xinyu.gov.cn/fgw/bjzfbmjsmfh/2023-02/14/content_72379a6f37a14e01a8fb4f08eac98a1c.shtml", "https://czj.xinyu.gov.cn/xysczj/cggzdtd/2024-12/10/content_a63c92a47f3d45b1a29257d6e0050708.shtml", "https://bjrd.gov.cn/xwzx/gsrd/202012/t20201222_2172612.html"]),
    "demo-360500-02": item(city="新余市", name="抱石公园", new_poi_id="verified-jx-xinyu-baoshi-park", category="城市公园", tags=["城市漫游", "休闲", "人文", "摄影"], duration=90, description="抱石公园是新余市城区以傅抱石文化命名的城市公园，包含园林景观、纪念性文化空间和休闲设施，适合城市漫游、短时休闲与人文参观。它属于城市公共空间，不与大型自然景区混同。", source_urls=["https://cgj.xinyu.gov.cn/cgj/gggs/2026-04/02/content_0ebb62c6beb6454c9b966e73d103bdcb.shtml", "https://www.xysmuseum.com/59.html", "https://m.thepaper.cn/newsDetail_forward_25227502"]),
    "demo-360500-04": item(city="新余市", name="罗坊会议红色景区", new_poi_id="verified-jx-xinyu-luofang-red-tourism", category="历史文化", tags=["历史文化", "红色文化", "人文", "研学", "乡村"], duration=180, description="罗坊会议红色景区位于新余市渝水区罗坊镇，串联罗坊会议旧址、兴国调查旧址及相关革命遗址，是国家4A级红色旅游景区，适合历史文化参观、爱国主义教育和研学。", source_urls=["https://jxxy.jxnews.com.cn/system/2021/05/13/019276913.shtml", "https://jxxy.jxnews.com.cn/system/2021/06/23/019317716.shtml", "https://bjrd.gov.cn/xwzx/gsrd/202012/t20201222_2172612.html"]),
    "demo-360500-05": item(city="新余市", name="宽心小镇", new_poi_id="verified-jx-xinyu-kuanxin-town", category="休闲度假", tags=["休闲", "城市漫游", "建筑", "人文", "摄影"], duration=150, description="宽心小镇位于新余仰天岗一带，以禅意山水、传统风格街区和休闲度假体验为主要特色，适合慢游、建筑摄影和短时休闲。园区经营内容、开放时间和活动以现场信息为准。", source_urls=["https://gxj.xinyu.gov.cn/gxw/jjyx/2023-08/11/content_e527a91aade74eaeae6465ecdd74b25c.shtml", "https://fgw.xinyu.gov.cn/fgw/bjzfbmjsmfh/2023-02/14/content_72379a6f37a14e01a8fb4f08eac98a1c.shtml", "https://you.ctrip.com/sight/xinyu878/134766662.html"]),
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"jiangxi-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"jiangxi-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(VERIFIED)
    with open_session() as session:
        pairs = list(session.execute(select(AttractionRow, CityRow).join(CityRow, CityRow.id == AttractionRow.city_id).join(ProvinceRow, ProvinceRow.id == CityRow.province_id).where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids)).order_by(AttractionRow.id)).all())
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches = []
        aliases = {"demo-360500-01": {"仙女湖风景区", "仙女湖风景名胜区"}}
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
        preview = [{"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "old_name": row.name, "new_name": VERIFIED[row.poi_id]["name"], "action": "verify"} for row in affected]
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(VERIFIED), "affected": preview, "source_urls": sorted({url for spec in VERIFIED.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-jiangxi-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "江西新余公开网页核验记录已写入；未调用高德，未新增数据库字段。"}


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

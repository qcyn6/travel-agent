"""核验贵州第一批演示景点；默认预览，``--commit`` 才会写库。"""

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


PROVINCE = "贵州省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-522600-01": item(city="黔东南苗族侗族自治州", name="西江千户苗寨", new_poi_id="verified-gz-qdn-xijiang", category="民俗文化", tags=["民族文化", "乡村", "人文", "夜游"], duration=240, description="西江千户苗寨是依山而建的苗族聚居村寨，以苗族建筑、农耕文化、民俗歌舞和夜景为主要体验，适合民族文化参观与村寨漫游。", source_urls=["https://www.mct.gov.cn/whzx/qgwhxxlb/gz/202409/t20240923_955368.htm", "https://www.qdn.gov.cn/zwfw_5872270/bmlqfw/lyfw_5872345/lyxl/202505/t20250528_87934149.html"]),
    "demo-522600-05": item(city="黔东南苗族侗族自治州", name="隆里古城", new_poi_id="verified-gz-qdn-longli", category="古镇与街区", tags=["历史文化", "古建", "人文", "城市漫游"], duration=180, description="隆里古城保存明代卫所城镇格局和明清民居建筑群，适合古城街巷漫游、历史文化参观和传统建筑观察。", source_urls=["https://www.qdn.gov.cn/zwgk_5871642/zdlyxxgk/whly_5872077/202111/t20211130_71845906.html", "https://www.gzszx.gov.cn/gzzxb/web/doc/detail/d_1359501164806176"]),
    "demo-522700-01": item(city="黔南布依族苗族自治州", name="荔波小七孔景区", new_poi_id="verified-gz-qn-xiaoqikong", category="自然山水", tags=["自然", "森林", "滨水", "地质景观"], duration=300, description="荔波小七孔景区以喀斯特森林、溪流、瀑布和小七孔古桥为主要体验，适合自然观光、摄影和生态游览。", source_urls=["https://www.mct.gov.cn/whzx/qgwhxxlb/gz/202401/t20240115_950817.htm", "https://www.qiannan.gov.cn/IGI/upload/file/2022/05/10/20220510044311520Erz.pdf"]),
    "demo-522700-02": item(city="黔南布依族苗族自治州", name="荔波大七孔景区", new_poi_id="verified-gz-qn-daqikong", category="自然山水", tags=["自然", "地质景观", "滨水", "摄影"], duration=180, description="荔波大七孔景区以峡谷、喀斯特地貌和水域景观为主要体验，适合峡谷观光、游船或步道漫游；具体线路以景区开放为准。", source_urls=["https://www.qiannan.gov.cn/IGI/upload/file/2022/05/10/20220510044311520Erz.pdf", "https://zh.wikipedia.org/wiki/%E6%A8%9F%E6%B1%9F"]),
    "demo-522700-04": item(city="黔南布依族苗族自治州", name="茂兰国家级自然保护区", new_poi_id="verified-gz-qn-maolan", category="生态动植物", tags=["自然", "森林", "生态", "研学"], duration=300, description="茂兰国家级自然保护区是荔波喀斯特世界自然遗产的重要组成，以喀斯特森林、生物多样性和生态科考为主要体验，进入保护区须遵守保护管理要求。", source_urls=["https://www.qiannan.gov.cn/IGI/upload/file/2022/05/10/20220510044311520Erz.pdf", "https://zh.wikipedia.org/wiki/%E8%B4%B5%E5%B7%9E%E8%8C%82%E5%85%B0%E5%9B%BD%E5%AE%B6%E7%BA%A7%E8%87%AA%E7%84%B6%E4%BF%9D%E6%8A%A4%E5%8C%BA"]),
    "demo-522300-02": item(city="黔西南布依族苗族自治州", name="马岭河峡谷", new_poi_id="verified-gz-qxn-malinghe", category="自然山水", tags=["自然", "山地", "地质景观", "滨水"], duration=240, description="马岭河峡谷以峡谷地貌、瀑布、溪流和喀斯特山地景观为主要体验，适合峡谷步行、自然观光和摄影。", source_urls=["https://lyj.guizhou.gov.cn/xwzx/tzgg/202302/P020230202546646752791.pdf", "https://www.mct.gov.cn/whzx/qgwhxxlb/gz/202401/t20240115_950817.htm"]),
    "demo-522300-03": item(city="黔西南布依族苗族自治州", name="万峰湖风景区", new_poi_id="verified-gz-qxn-wanfenghu", category="滨水景观", tags=["自然", "滨水", "山地", "摄影"], duration=180, description="万峰湖以曲折湖岸、岛屿、山水和水上休闲景观为主要体验，适合湖泊观光、摄影和休闲度假。", source_urls=["https://lyj.guizhou.gov.cn/xwzx/tzgg/202302/P020230202546646752791.pdf", "https://www.mct.gov.cn/whzx/qgwhxxlb/gz/201810/t20181012_835351.htm"]),
    "demo-522300-04": item(city="黔西南布依族苗族自治州", name="双乳峰景区", new_poi_id="verified-gz-qxn-shuangrufeng", category="自然山水", tags=["自然", "地质景观", "摄影", "研学"], duration=150, description="双乳峰景区以喀斯特峰丛、峰林地貌和布依族地域文化为主要体验，适合地质景观观赏、摄影与民族文化了解。", source_urls=["https://www.mct.gov.cn/whzx/qgwhxxlb/gz/201810/t20181012_835351.htm", "https://www.neac.gov.cn/seac/ztzl/byz/gk.shtml"]),
}


DELETE: dict[str, dict[str, str]] = {
    "demo-522300-05": {"city": "黔西南布依族苗族自治州", "name": "布依族村寨文化体验区", "reason": "泛化的体验区概念，未对应具体村寨或独立景区入口，删除以免把民族文化大类当作单一 POI。"},
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"guizhou-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"guizhou-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(VERIFIED) + tuple(DELETE)
    with open_session() as session:
        pairs = list(session.execute(select(AttractionRow, CityRow).join(CityRow, CityRow.id == AttractionRow.city_id).join(ProvinceRow, ProvinceRow.id == CityRow.province_id).where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids)).order_by(AttractionRow.id)).all())
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches = []
        for poi_id, spec in {**VERIFIED, **DELETE}.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name != spec["name"] or city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected": f"{spec['city']} / {spec['name']}", "actual": f"{city} / {row.name}"})
        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = [row.poi_id for row in session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(new_ids)))]
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))
        affected = [found[poi_id][0] for poi_id in target_ids]
        preview = [{"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "action": "verify" if row.poi_id in VERIFIED else "delete"} for row in affected]
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "source_urls": sorted({url for spec in VERIFIED.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-guizhou-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}, "deletion_reasons": DELETE}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        for poi_id in DELETE:
            session.delete(found[poi_id][0])
        session.commit()
    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        old_ids = list(VERIFIED) + list(DELETE)
        index.collection.delete(ids=old_ids)
        vectors_deleted = len(old_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "贵州第一批公开网页核验记录已写入；未调用高德，未新增数据库字段。"}


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

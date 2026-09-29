"""核验辽宁演示景点；默认预览，``--commit`` 才写库。"""

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


PROVINCE = "辽宁省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-210600-03": item(city="丹东市", name="凤凰山风景区", new_poi_id="verified-ln-dandong-phoenix-mountain", category="自然山水", tags=["自然", "山地", "森林", "摄影", "宗教文化"], duration=240, description="凤凰山风景区位于丹东市凤城市，以山岳、森林、栈道和宗教人文景观为主要体验，核心景区包含西山、东山和古城等片区，适合登山观景与摄影。游览强度和开放设施应结合天气及景区公告安排。", source_urls=["https://whly.ln.gov.cn/whly/wlzt/sjly/ajjq/2025120115414032518/index.shtml", "https://www.ln.gov.cn/web/sqgk/whly/ajjq/dd/index.shtml"]),
    "demo-210600-04": item(city="丹东市", name="天桥沟景区", new_poi_id="verified-ln-dandong-tianqiaogou", category="生态动植物", tags=["自然", "森林", "山地", "摄影", "休闲"], duration=240, description="天桥沟景区位于丹东市宽甸县天桥沟森林公园内，以森林、山地、溪谷和秋季红枫景观为主，兼有森林康养和滑雪度假体验，适合自然观光、摄影和休闲。", source_urls=["https://whly.ln.gov.cn/whly/wlzt/sjly/ajjq/2025120115465335241/index.shtml", "https://www.ln.gov.cn/web/ywdt/tjdt/2023091110200218264/index.shtml", "https://mct.gov.cn/preview/whzx/qgwhxxlb/ln/202201/t20220110_930335.htm"]),
    "demo-210900-04": item(city="阜新市", name="阜新万人坑死难矿工纪念馆", new_poi_id="verified-ln-fuxin-wankeng-memorial", category="近现代建筑", tags=["历史文化", "红色文化", "工业遗产", "研学", "人文"], duration=120, description="阜新万人坑死难矿工纪念馆位于阜新市太平区，是全国重点文物保护单位和国家级抗战纪念设施，展示死难矿工遗址、史实展陈和文物保护成果，适合历史文化与工业遗产研学。参观安排以纪念馆开放公告为准。", source_urls=["https://www.fxtp.gov.cn/content/2021/96024.html", "https://gxj.fuxin.gov.cn/content/2022/691521.html", "https://whly.fuxin.gov.cn/content/2024/319864.html"]),
    "demo-211400-02": item(city="葫芦岛市", name="觉华岛旅游度假区", new_poi_id="verified-ln-huludao-juehua-island", category="滨水景观", tags=["滨水", "岛屿", "休闲", "历史文化", "摄影"], duration=240, description="觉华岛旅游度假区位于葫芦岛市兴城市东南海域，以海岛岸线、滨海休闲和辽金佛教文化遗存为主要体验，适合海岛观光与度假。船班、天气和岛上交通会影响游览安排。", source_urls=["https://www.ln.gov.cn/web/qmzx/lnkc/gzzsjdkczj/2025021910214673946/index.shtml", "https://fgw.ln.gov.cn/fgw/attachDir/2023/10/2023101314153823523.pdf", "https://wsb.ln.gov.cn/wsb/xxgk/zxdt/2025042309320555687/2025042314182554818.pdf"]),
    "demo-211400-03": item(city="葫芦岛市", name="九门口长城景区", new_poi_id="verified-ln-huludao-jiamengkou-great-wall", category="历史古迹", tags=["历史文化", "古建", "建筑", "摄影", "滨水"], duration=180, description="九门口长城景区位于葫芦岛市绥中县，是万里长城中具有代表性的水上长城段，适合长城建筑、历史文化和山水景观观赏。游览应遵守长城保护规定并按景区开放路线行走。", source_urls=["https://whly.ln.gov.cn/whly/wlzt/zcgjwhgy/jsdt/2023081410441078051/index.shtml", "https://www.ln.gov.cn/web/ywdt/zymtkln/2025071509193873424/index.shtml", "https://fgw.ln.gov.cn/fgw/attachDir/2023/10/2023101314153823523.pdf"]),
    "demo-211400-04": item(city="葫芦岛市", name="龙潭大峡谷自然风景区", new_poi_id="verified-ln-huludao-longtan-canyon", category="自然山水", tags=["自然", "山地", "地质景观", "摄影", "露营"], duration=240, description="龙潭大峡谷自然风景区位于葫芦岛市建昌县，以峡谷、山地和溪流地貌为主要体验，适合自然观光、徒步和摄影，景区已纳入当地国家A级景区建设与旅游线路。具体步道和天气条件以景区公告为准。", source_urls=["https://www.ln.gov.cn/web/zwgkx/zfgzbg/shizfgzbg/hlds/767BEBC0668D4D4D9F34213EA968ACE9/index.shtml", "https://whly.ln.gov.cn/whly/zfxxgk/fdzdgknr/jyta/szxta/szxsejwchy2022n/2022101015402782026/index.shtml", "https://fgw.ln.gov.cn/fgw/attachDir/2023/10/2023101314153823523.pdf"]),
    "demo-211400-05": item(city="葫芦岛市", name="葫芦山庄乡村旅游区", new_poi_id="verified-ln-huludao-hulu-village", category="民俗文化", tags=["民俗", "人文", "博物馆", "休闲", "美食"], duration=180, description="葫芦山庄乡村旅游区位于葫芦岛市龙港区，以葫芦文化、关东民俗、展馆、历史街区和乡村休闲为主要体验，适合民俗文化参观、亲子研学与慢游。园区开放内容以现场运营为准。", source_urls=["https://www.ndrc.gov.cn/xwdt/ztzl/qgxclydxal/mswhytx/202004/t20200423_1226473.html", "https://www.ln.gov.cn/web/zwgkx/tjgb2/shizfgzbg/hlds/17169A519B6244C7856C49FF2F232C44/index.shtml", "https://zhuanti.mct.gov.cn/xcss2024_xcygj/liaoning/detail/7216.html"]),
}


DELETE: dict[str, dict[str, Any]] = {
    "demo-210900-05": {"city": "阜新市", "name": "蒙古贞文化产业园", "reason": "公开规划资料对应的是正在建设的‘蒙古贞·希日塔拉’民族文化旅游产业园，未核验到已开放且可独立规划的景点入口，删除泛化项目条目。", "source_urls": ["https://www.fuxin.gov.cn/khcs/file/2024-12-11/17339037633154028e4928e7a358a7060193b4b7c7737a38.pdf", "https://www.fmx.gov.cn/content/2024/294278.html"]},
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"liaoning-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"liaoning-verified-batch-before-{stamp}-{suffix}.json"
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
        aliases = {"demo-210600-03": {"凤凰山风景区", "丹东凤凰山风景区"}, "demo-210600-04": {"天桥沟森林公园", "天桥沟景区"}, "demo-210900-04": {"阜新万人坑遗址", "阜新万人坑死难矿工纪念馆"}, "demo-211400-02": {"觉华岛", "觉华岛旅游度假区"}, "demo-211400-03": {"九门口长城", "九门口长城景区"}, "demo-211400-04": {"龙潭大峡谷", "龙潭大峡谷自然风景区"}, "demo-211400-05": {"葫芦山庄", "葫芦山庄乡村旅游区"}}
        for poi_id, spec in {**VERIFIED, **DELETE}.items():
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
        preview = [{"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "old_name": row.name, "new_name": VERIFIED[row.poi_id]["name"] if row.poi_id in VERIFIED else None, "action": "verify" if row.poi_id in VERIFIED else "delete"} for row in affected]
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "source_urls": sorted({url for spec in [*VERIFIED.values(), *DELETE.values()] for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-liaoning-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}, "deletion_reasons": DELETE}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["name"]
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
        index.collection.delete(ids=list(target_ids))
        vectors_deleted = len(target_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "辽宁公开网页核验记录已写入；未调用高德，未新增数据库字段。"}


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

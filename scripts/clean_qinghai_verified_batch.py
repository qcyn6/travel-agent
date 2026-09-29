"""核验青海演示景点并删除不适合进入规划库的保护区/未开放条目。"""

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


PROVINCE = "青海省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-632600-05": item(
        city="果洛藏族自治州",
        name="格萨尔林卡旅游景区",
        new_poi_id="verified-qh-guoluo-dari-gesar-linka",
        category="历史文化",
        tags=["历史文化", "人文", "雕塑艺术", "摄影", "高原"],
        duration=120,
        description="格萨尔林卡旅游景区位于果洛州达日县城附近山地，以格萨尔雕像、浮雕群和黄河谷地景观为主要体验，适合格萨尔史诗文化了解、观景与摄影。高原天气和道路条件变化较快，具体游览按当地开放信息安排。",
        source_urls=[
            "https://hzjl.sh.gov.cn/n1309/20240222/0af6565e561b400088adafc19809b9ae.html",
            "https://hzjl.sh.gov.cn/n1309/20240927/cb0352122a6a442fb8bc49aa729dc100.html",
        ],
    ),
    "demo-632700-02": item(
        city="玉树藏族自治州",
        name="文成公主庙",
        new_poi_id="verified-qh-yushu-wencheng-temple",
        category="宗教建筑",
        tags=["历史文化", "宗教文化", "古建", "人文", "摄影"],
        duration=90,
        description="文成公主庙位于玉树州玉树市巴塘乡，依山崖而建，是唐蕃古道上的历史文化遗存，保留岩壁造像、藏式建筑和汉藏文化交流记忆，适合安静参观与人文摄影。",
        source_urls=[
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/wldt/202407/t20240725_427246.html",
            "https://www.qinghai.gov.cn/dmqh/system/2015/08/12/010174685.shtml",
            "https://qh.people.com.cn/n2/2022/0517/c181467-35273050.html",
        ],
    ),
    "demo-632700-03": item(
        city="玉树藏族自治州",
        name="新寨嘉那嘛呢石经城",
        new_poi_id="verified-qh-yushu-jiana-mani-stone-city",
        category="宗教建筑",
        tags=["历史文化", "宗教文化", "人文", "摄影"],
        duration=120,
        description="新寨嘉那嘛呢石经城位于玉树市结古镇附近的新寨村，以持续雕刻和堆叠的嘛呢石、佛堂、经轮和藏传佛教文化为主要体验，是玉树重要的宗教文化遗产。参观时应尊重当地宗教礼仪并按现场管理路线游览。",
        source_urls=[
            "https://www.guinan.gov.cn/lnb/zc1__zwgk/zc/aztfl/smhgly/content_18609291",
            "https://www.mct.gov.cn/whzx/qgwhxxlb/qh/201205/t20120515_792818.htm",
            "https://www.qhrd.gov.cn/yajy/202407/t20240704_219918.html",
        ],
    ),
    "demo-632700-04": item(
        city="玉树藏族自治州",
        name="巴塘草原",
        new_poi_id="verified-qh-yushu-batang-grassland",
        category="自然山水",
        tags=["自然", "草原", "高原", "摄影", "休闲"],
        duration=150,
        description="巴塘草原位于玉树州玉树市巴塘乡一带，是唐蕃古道沿线的高原草原景观，适合草原观景、摄影和了解牧区生活。草原为广域自然空间，具体停留点、道路和天气条件应以当日信息为准。",
        source_urls=[
            "https://qh.people.com.cn/n2/2022/0517/c181467-35273050.html",
            "https://qh.weather.com.cn/bdly/lyjdjs/05/510118.shtml",
            "https://www.qinghai.gov.cn/dmqh/system/2015/08/12/010174685.shtml",
        ],
    ),
}


DELETE: dict[str, dict[str, Any]] = {
    "demo-632600-01": {"city": "果洛藏族自治州", "name": "年保玉则生态景区", "reason": "年保玉则核心区属于保护地，公开信息显示景区已停止对外开放；不作为当前可规划的普通景点。", "source_urls": ["https://wlt.xizang.gov.cn/xwzx_69/xydt/202109/t20210901_258014.html", "https://app.xinhuanet.com/news/article.html?articleId=83f14d0bde6b46f538731da4890365fd"]},
    "demo-632600-02": {"city": "果洛藏族自治州", "name": "阿尼玛卿山", "reason": "阿尼玛卿核心区生态保护和旅游基础设施拆除信息明确，不具备常规旅游景区条件，删除以免规划到未开放区域。", "source_urls": ["https://qhms.qhnu.edu.cn/html/web/xcly/jqjd/1523554598650568705.html", "https://m.bobtrunell.com/whlyt/tzgg/26367.html"]},
    "demo-632600-03": {"city": "果洛藏族自治州", "name": "黄河源头景区", "reason": "黄河源园区属于三江源国家公园保护管理单元，核心区不是普通开放景点，现有名称也无法对应独立可预约入口，删除避免把保护区片区当作景点。", "source_urls": ["https://sjy.qinghai.gov.cn/zjgy/", "https://qh.people.com.cn/n2/2022/0516/c403444-35270677.html"]},
    "demo-632600-04": {"city": "果洛藏族自治州", "name": "班玛原始森林", "reason": "玛可河原始森林属于三江源自然保护区核心区，公开管理信息禁止将未开发原始森林作为普通旅游线路，删除泛化保护区 POI。", "source_urls": ["https://www.qhrd.gov.cn/yajy/202204/t20220412_201660.html", "https://www.qhnews.com/index/system/2024/11/04/030298963.shtml"]},
    "demo-632700-01": {"city": "玉树藏族自治州", "name": "三江源国家公园玉树片区", "reason": "这是国家公园的保护管理片区而非独立景点入口，缺少可直接规划的开放游览范围，删除避免与具体景点重叠。", "source_urls": ["https://sjy.qinghai.gov.cn/zjgy/", "https://sjy.qinghai.gov.cn/storage/app/media/uploaded-files/20180309110108_.pdf"]},
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"qinghai-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"qinghai-verified-batch-before-{stamp}-{suffix}.json"
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
        aliases = {
            "demo-632600-05": {"格萨尔林卡文化园", "格萨尔林卡", "格萨尔林卡旅游景区"},
            "demo-632600-03": {"黄河源头景区", "黄河源头景观区"},
        }
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
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-qinghai-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}, "deletion_reasons": DELETE}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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
        index.collection.delete(ids=list(VERIFIED) + list(DELETE))
        vectors_deleted = len(target_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "青海公开网页核验与不可规划保护区清理已写入；未调用高德，未新增数据库字段。"}


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

"""核验湖北第一批演示景点（仙桃、天门、潜江）。

只使用政府/文旅部门等公开网页，更新 attractions 已有字段，不调用高德或新增字段。
默认预览；``--commit`` 才会备份并写入。
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


PROVINCE = "湖北省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str], new_name: str | None = None) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "new_name": new_name or name, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-429004-02": item(city="仙桃市", name="梦里水乡文化旅游区", new_poi_id="verified-hb-xiantao-mengli", category="综合景区", tags=["自然", "生态", "乡村", "休闲"], duration=240, description="梦里水乡文化旅游区以水上森林、湿地、乡村民俗和水乡休闲为主要体验，适合生态观光、亲子游和慢节奏度假。", source_urls=["https://zhuanti.mct.gov.cn/mmyghcyt2023/dcxc/detail/5052.html", "https://wlt.hubei.gov.cn/bmdt/dtyw/xt/201911/t20191121_1358034.shtml"]),
    "demo-429004-03": item(city="仙桃市", name="陈友谅故居", new_poi_id="verified-hb-xiantao-chenyouliang", category="历史古迹", tags=["历史文化", "人文", "博物馆", "城市漫游"], duration=90, description="仙桃沔街一带设有陈友谅纪念馆等名人文化展示点，适合了解沔阳历史、人物文化和地方民俗。", source_urls=["https://wlt.hubei.gov.cn/bmdt/szyw/xt/201912/t20191204_1677333.shtml", "https://dsjyh02.llb.chutianyun.gov.cn/zjxt/"], new_name="陈友谅纪念馆"),
    "demo-429004-04": item(city="仙桃市", name="沔城遗址", new_poi_id="verified-hb-xiantao-miancheng", category="历史古迹", tags=["历史文化", "古建", "人文", "研学"], duration=120, description="沔城遗址位于仙桃沔城一带，是具有地方历史文化价值的古文化遗址，适合结合古城格局、遗址和地方文史进行参观。", source_urls=["https://zhuanti.mct.gov.cn/mmyghcyt2023/dcxc/detail/5052.html", "https://zh.wikipedia.org/wiki/%E4%BB%99%E6%A1%83%E5%B8%82%E5%90%84%E7%BA%A7%E6%96%87%E7%89%A9%E4%BF%9D%E6%8A%A4%E5%8D%95%E4%BD%8D%E5%88%97%E8%A1%A8"]),
    "demo-429004-05": item(city="仙桃市", name="仙桃排湖风景区", new_poi_id="verified-hb-xiantao-paihu", category="滨水景观", tags=["自然", "滨水", "生态", "休闲"], duration=180, description="排湖是仙桃重要的淡水湖泊与水乡旅游资源，适合湖区观景、骑行、乡村休闲和生态体验。", source_urls=["https://wlt.hubei.gov.cn/bmdt/dtyw/xt/201911/t20191121_1677333.shtml", "https://wlt.hubei.gov.cn/bmdt/szyw/xt/201912/t20191204_1677333.shtml"]),
    "demo-429006-01": item(city="天门市", name="茶圣故里园", new_poi_id="verified-hb-tianmen-luyu-park", category="历史古迹", tags=["历史文化", "人文", "博物馆", "研学"], duration=180, description="茶圣故里园是天门集中展示陆羽生平和茶文化的国家级旅游景区，包含陆羽故园、博物馆等功能片区，适合茶文化与地方历史研学。", source_urls=["https://www.tianmen.gov.cn/zwgk/bmhxzxxgkml/bm/swhhlyj/zfxxgk/fdzdgknr/gysyjs/ly1/202209/t20220906_4295691.shtml"]),
    "demo-429006-02": item(city="天门市", name="石家河遗址", new_poi_id="verified-hb-tianmen-shijiahe", category="历史古迹", tags=["历史文化", "研学", "人文", "建筑"], duration=180, description="石家河遗址是长江中游重要的新石器时代遗址，出土有石器、玉器、陶器等文物，适合考古与史前文化研学。", source_urls=["https://www.tianmen.gov.cn/zjtm/"]),
    "demo-429006-05": item(city="天门市", name="天门东湖公园", new_poi_id="verified-hb-tianmen-donghu", category="城市公园", tags=["自然", "滨水", "城市漫游", "休闲"], duration=120, description="天门东湖公园是城市湖泊休闲空间，适合散步、湖滨观景和短时城市休闲；作为城市公园按次要游览点处理。", source_urls=["https://www.tianmen.gov.cn/zjtm/"]),
    "demo-429005-01": item(city="潜江市", name="曹禺文化旅游区", new_poi_id="verified-hb-qianjiang-caoyu", category="综合景区", tags=["历史文化", "人文", "博物馆", "研学"], duration=180, description="曹禺文化旅游区以戏剧家曹禺及其文学创作为主题，融合文化展陈、戏剧体验和地方城市文化，适合人文参观与研学。", source_urls=["https://www.hbqj.gov.cn/zmhd/xwfbh/202209/t20220920_4314618.html", "https://www.hbqj.gov.cn/zjqj/"]),
    "demo-429005-02": item(city="潜江市", name="潜江龙虾小镇", new_poi_id="verified-hb-qianjiang-lobster", category="古镇与街区", tags=["美食", "城市漫游", "休闲", "人文"], duration=150, description="潜江龙虾城以小龙虾产业、餐饮、美食文化和休闲街区为主要体验，适合地方美食、城市漫游和产业文化了解。", source_urls=["https://www.hbqj.gov.cn/zmhd/xwfbh/202209/t20220920_4314618.html", "https://nyt.hubei.gov.cn/bmdt/yw/mtksn/202201/t20220105_3951492.shtml"], new_name="潜江龙虾城"),
    "demo-429005-03": item(city="潜江市", name="返湾湖风景区", new_poi_id="verified-hb-qianjiang-fanwanhu", category="生态动植物", tags=["自然", "生态", "滨水", "休闲"], duration=180, description="返湾湖国家湿地公园以湖泊湿地、生态绿道和运动休闲为主要体验，适合观景、慢跑、骑行和湿地科普。", source_urls=["https://www.hbqj.gov.cn/zmhd/xwfbh/202209/t20220920_4314618.html", "https://www.hbqj.gov.cn/zjqj/"]),
    "demo-429005-04": item(city="潜江市", name="章华台遗址", new_poi_id="verified-hb-qianjiang-zhanghuatai", category="历史古迹", tags=["历史文化", "古建", "人文", "研学"], duration=120, description="章华台是潜江楚文化历史遗存，与龙湾考古遗址公园共同构成楚文化遗址展示资源，适合历史文化与考古研学。", source_urls=["https://www.hbqj.gov.cn/zmhd/xwfbh/202209/t20220920_4314618.html", "https://www.hbqj.gov.cn/zjqj/tzqj/tzzn/201705/t20170527_2090826.html"]),
    "demo-429005-05": item(city="潜江市", name="兴隆水利枢纽生态园", new_poi_id="verified-hb-qianjiang-xinglong", category="生态动植物", tags=["自然", "滨水", "生态", "研学"], duration=150, description="兴隆水利枢纽风景区以水利工程景观、江汉平原水系和生态湿地为主要体验，适合工程科普、滨水观景和生态休闲。", source_urls=["https://www.hbqj.gov.cn/zjqj/lyqj/jdjs/202109/t20210906_3741970.html", "https://www.hbqj.gov.cn/zjqj/"]) ,
}


DELETE: dict[str, dict[str, str]] = {
    "demo-429006-03": {"city": "天门市", "name": "陆羽纪念馆", "reason": "茶圣故里园官方说明已将陆羽纪念馆列为园区组成/主要景点，删除子景点重复行，保留父级景区。"},
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"hubei-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"hubei-verified-batch-before-{stamp}-{suffix}.json"
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
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-hubei-batch", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}, "deletion_reasons": DELETE}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["new_name"]
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
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "湖北第一批公开网页核验记录已写入；未调用高德，未新增数据库字段。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="备份并写入数据库；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

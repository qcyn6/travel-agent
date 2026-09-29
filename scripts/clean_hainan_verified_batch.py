"""核验并清理海南第一批景点记录。

本脚本沿用云南清洗规则：只更新 attractions 已有规划字段，不新增字段、不调用高德。
第一批只处理有政府部门、国家机构或官方文旅页面可交叉核对的记录；尚未找到足够
证据的演示记录保留到下一批，不用猜测内容填充。

默认只预览；传入 ``--commit`` 才会先备份并写库。公开网页链接只保存在本脚本和
运行审计输出中，不写入数据库（用户已明确不要新增来源字段）。
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


PROVINCE = "海南省"


def _item(
    *,
    city: str,
    name: str,
    new_poi_id: str,
    category: str,
    tags: list[str],
    duration: int,
    description: str,
    source_urls: list[str],
) -> dict[str, Any]:
    return {
        "city": city,
        "name": name,
        "new_poi_id": new_poi_id,
        "category": category,
        "tags": tags,
        "duration": duration,
        "description": description,
        "source_urls": source_urls,
    }


# 这些名称必须与 demo 行的 city/name 同时匹配，避免误改同名景点。
VERIFIED: dict[str, dict[str, Any]] = {
    "demo-460400-03": _item(
        city="儋州市",
        name="石花水洞地质公园",
        new_poi_id="verified-hn-danzhou-shihuashuidong",
        category="地质景观",
        tags=["自然", "地质景观", "研学", "摄影"],
        duration=180,
        description="石花水洞以喀斯特溶洞、石花等地质景观为主要体验，适合地质科普、洞穴观赏和自然摄影。建议按开放时段与洞内步行条件安排游览。",
        source_urls=[
            "https://www.danzhou.gov.cn/danzhou/ywdt/jrdz/202304/t20230427_3407063.html",
            "https://rd.danzhou.gov.cn/h-nd-2169.html",
        ],
    ),
    "demo-460400-04": _item(
        city="儋州市",
        name="蓝洋温泉度假区",
        new_poi_id="verified-hn-danzhou-lanyang-hot-spring",
        category="温泉",
        tags=["休闲", "温泉", "自然", "生态"],
        duration=180,
        description="蓝洋温泉以温泉地热和周边森林环境为主要体验，适合泡汤、休闲和短时放松。实际停留时间应结合营业时段与预约规则安排。",
        source_urls=[
            "https://zhuanti.mct.gov.cn/csxz2022/hainan/detail/1529.html",
            "https://rd.danzhou.gov.cn/h-nd-2169.html",
        ],
    ),
    "demo-460400-05": _item(
        city="儋州市",
        name="峨蔓火山海岸",
        new_poi_id="verified-hn-danzhou-eman-coast",
        category="地质景观",
        tags=["自然", "地质景观", "滨水", "摄影"],
        duration=180,
        description="峨蔓火山海岸以海岸火山地貌、礁石和滨海村落景观为主要体验，适合地质观察、海岸摄影和日落漫游。建议根据潮汐与天气安排。",
        source_urls=[
            "https://www.danzhou.gov.cn/danzhou/ywdt/jrdz/202304/t20230427_3407063.html",
            "https://rd.danzhou.gov.cn/h-nd-2169.html",
        ],
    ),
    "demo-469001-01": _item(
        city="五指山市",
        name="五指山热带雨林风景区",
        new_poi_id="verified-hn-wuzhishan-rainforest",
        category="自然山水",
        tags=["自然", "森林", "山地", "生态", "摄影"],
        duration=240,
        description="五指山热带雨林区域以山地雨林、溪流和热带生态景观为主要体验，适合森林徒步、自然观察和摄影。游览强度与开放路线需以现场公告为准。",
        source_urls=[
            "https://en.hainan.gov.cn/hainan/zxxx/202106/81861a1288a9440a8cf153f2e454b985.shtml",
            "https://www.hntrnp.com/",
        ],
    ),
    "demo-469001-02": _item(
        city="五指山市",
        name="海南民族博物馆",
        new_poi_id="verified-hn-wuzhishan-ethnic-museum",
        category="博物馆与展馆",
        tags=["历史文化", "博物馆", "民族文化", "研学"],
        duration=120,
        description="海南民族博物馆以海南黎族、苗族等民族历史、生产生活和传统文化展示为主要内容，适合室内参观与民族文化了解。",
        source_urls=[
            "https://en.hainan.gov.cn/hainan/zxxx/202106/81861a1288a9440a8cf153f2e454b985.shtml",
        ],
    ),
    "demo-469001-03": _item(
        city="五指山市",
        name="五指山革命根据地纪念园",
        new_poi_id="verified-hn-wuzhishan-revolutionary-park",
        category="历史古迹",
        tags=["历史文化", "红色文化", "人文", "研学"],
        duration=120,
        description="五指山革命根据地纪念园以琼崖革命历史和五指山革命根据地相关展陈、遗址纪念为主要内容，适合红色文化学习与纪念参观。",
        source_urls=[
            "https://tyt.hainan.gov.cn/twjr/jnss1/202003/5aa5deed7757421d817abb77edb7ff88.shtml?ddtab=true",
            "https://en.hainan.gov.cn/hainan/zxxx/202106/81861a1288a9440a8cf153f2e454b985.shtml",
        ],
    ),
    "demo-469001-04": _item(
        city="五指山市",
        name="黎峒文化园",
        new_poi_id="verified-hn-wuzhishan-lidong-culture",
        category="民俗文化",
        tags=["民族文化", "人文", "历史文化", "研学"],
        duration=150,
        description="黎峒文化园以黎族历史、村落文化和传统生活展示为主要体验，适合民族文化参观、民俗了解与慢游。具体演出或活动以园区安排为准。",
        source_urls=[
            "https://en.hainan.gov.cn/hainan/zxxx/202106/81861a1288a9440a8cf153f2e454b985.shtml",
        ],
    ),
    "demo-469001-05": _item(
        city="五指山市",
        name="牙胡梯田",
        new_poi_id="verified-hn-wuzhishan-yahu-terraces",
        category="自然山水",
        tags=["自然", "乡村", "生态", "摄影"],
        duration=150,
        description="牙胡梯田以山地梯田、乡村景观和季节性田园风貌为主要体验，适合摄影、乡村漫游和生态观察。景观效果受季节与天气影响较大。",
        source_urls=[
            "https://smzw.hainan.gov.cn/smzw/zwdt/202606/de87bd28f4694979af49c6b70fdb15e8.shtml",
            "https://en.hainan.gov.cn/hainan/zxxx/202106/81861a1288a9440a8cf153f2e454b985.shtml",
        ],
    ),
    "demo-469005-01": _item(
        city="文昌市",
        name="文昌航天发射场",
        new_poi_id="verified-hn-wenchang-space-launch",
        category="综合景区",
        tags=["研学", "人文", "摄影", "自然"],
        duration=180,
        description="文昌航天发射场是我国面向大型运载火箭发射的重要航天设施，相关参观区域以航天科普、发射场外观和滨海环境体验为主，开放范围须以官方安排为准。",
        source_urls=[
            "https://www.cmse.gov.cn/gygc/xtzc/hnfscxt/",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469005-02": _item(
        city="文昌市",
        name="东郊椰林",
        new_poi_id="verified-hn-wenchang-dongjiao-coconut",
        category="滨水景观",
        tags=["自然", "滨水", "生态", "摄影"],
        duration=150,
        description="东郊椰林以椰林、海岸和热带滨海村落景观为主要体验，适合海边散步、摄影和休闲。建议结合日照、潮汐及天气安排停留。",
        source_urls=[
            "https://news.hainan.net/zixun/2025/12/31/4800763.shtml",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469005-04": _item(
        city="文昌市",
        name="文昌孔庙",
        new_poi_id="verified-hn-wenchang-confucian-temple",
        category="历史古迹",
        tags=["历史文化", "古建", "人文", "建筑"],
        duration=120,
        description="文昌孔庙以古建筑格局、儒学文化和地方历史展示为主要体验，适合古建参观、人文了解和城市文化漫游。",
        source_urls=[
            "https://news.hainan.net/zixun/2025/12/31/4800763.shtml",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469005-05": _item(
        city="文昌市",
        name="月亮湾海岸",
        new_poi_id="verified-hn-wenchang-moon-bay",
        category="滨水景观",
        tags=["自然", "滨水", "摄影", "生态"],
        duration=150,
        description="月亮湾海岸以开阔沙滩、海岸线和热带滨海景观为主要体验，适合海岸漫步、摄影和休闲。实际下水及游玩活动需遵守现场安全提示。",
        source_urls=[
            "https://news.hainan.net/zixun/2025/12/31/4800763.shtml",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469007-01": _item(
        city="东方市",
        name="鱼鳞洲风景区",
        new_poi_id="verified-hn-dongfang-yulinzhou",
        category="滨水景观",
        tags=["自然", "滨水", "摄影", "城市漫游"],
        duration=150,
        description="鱼鳞洲以礁石海岸、灯塔和开阔海景为主要体验，适合海岸摄影、日落观景和短时休闲漫游。",
        source_urls=[
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469007-02": _item(
        city="东方市",
        name="俄贤岭生态旅游区",
        new_poi_id="verified-hn-dongfang-exianling",
        category="自然山水",
        tags=["自然", "山地", "森林", "生态", "摄影"],
        duration=240,
        description="俄贤岭以山地、峡谷、森林和水库景观为主要体验，适合自然徒步、生态观察和摄影。游览线路与体力要求应以景区开放情况为准。",
        source_urls=[
            "https://www.exianling.com/",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469007-03": _item(
        city="东方市",
        name="白查村船形屋",
        new_poi_id="verified-hn-dongfang-baicha",
        category="民俗文化",
        tags=["民族文化", "乡村", "人文", "建筑", "摄影"],
        duration=120,
        description="白查村船形屋以黎族传统民居、村落空间和民族建筑文化为主要体验，适合村落漫游、传统建筑观察与人文摄影。",
        source_urls=[
            "https://www.exianling.com/",
            "https://www.hainan.gov.cn/hainan/xgwjt/202402/e43343bf034d42d99ecdd49454fcdd15.shtml?ddtab=true",
        ],
    ),
    "demo-469007-04": _item(
        city="东方市",
        name="大广坝水库景区",
        new_poi_id="verified-hn-dongfang-daguangba",
        category="滨水景观",
        tags=["自然", "滨水", "生态", "摄影"],
        duration=150,
        description="大广坝水库以库区水面、山地和周边生态景观为主要体验，适合观景、摄影和近郊休闲。具体可进入区域以现场管理为准。",
        source_urls=[
            "https://www.exianling.com/",
        ],
    ),
}


DELETE: dict[str, dict[str, str]] = {
    "demo-469007-05": {
        "city": "东方市",
        "name": "东方滨海休闲带",
        "reason": "名称是区域性概念，未找到对应的独立景区或可核验的单一开放入口，按云南规则删除非独立 POI。",
    },
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"hainan-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"hainan-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(VERIFIED) + tuple(DELETE)
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids))
                .order_by(AttractionRow.id)
            ).all()
        )
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches: list[dict[str, Any]] = []
        for poi_id, spec in VERIFIED.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name != spec["name"] or city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected": f"{spec['city']} / {spec['name']}", "actual": f"{city} / {row.name}"})
        for poi_id, spec in DELETE.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name != spec["name"] or city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected": f"{spec['city']} / {spec['name']}", "actual": f"{city} / {row.name}"})

        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = [row.poi_id for row in session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(new_ids)))]
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))

        affected = [found[poi_id][0] for poi_id in target_ids]
        preview = [
            {"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "action": "verify" if row.poi_id in VERIFIED else "delete"}
            for row in affected
        ]
        if not commit:
            return {
                "province": PROVINCE,
                "mode": "preview",
                "verified_replacements": len(VERIFIED),
                "deletions": len(DELETE),
                "affected": preview,
                "source_urls": sorted({url for spec in VERIFIED.values() for url in spec["source_urls"]}),
                "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。",
            }

        backup_path = _backup_path(backup_dir)
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "province": PROVINCE,
                    "reason": "public-source-verified-hainan-batch",
                    "rows": [_payload(row) for row in affected],
                    "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()},
                    "deletion_reasons": DELETE,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

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

    settings = get_settings()
    vectors_deleted = 0
    vector_error = None
    try:
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        old_ids = list(VERIFIED) + list(DELETE)
        index.collection.delete(ids=old_ids)
        vectors_deleted = len(old_ids)
    except Exception as exc:  # pragma: no cover - depends on local Chroma state
        vector_error = f"{type(exc).__name__}: {exc}"
    return {
        "province": PROVINCE,
        "mode": "commit",
        "verified_replacements": len(VERIFIED),
        "deletions": len(DELETE),
        "affected": preview,
        "backup_path": str(backup_path),
        "vectors_deleted": vectors_deleted,
        "vector_error": vector_error,
        "message": "海南第一批公开网页核验记录已写入；未调用高德，未新增数据库字段。",
    }


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

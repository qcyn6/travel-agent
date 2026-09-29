"""核验西藏剩余演示景点；默认预览，``--commit`` 才写库。

本批只处理能用西藏自治区政府、文旅部门或自然资源部门公开页面核对的
记录。来源链接留在脚本和备份审计中，不写入数据库，也不调用高德。
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


PROVINCE = "西藏自治区"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str]) -> dict[str, Any]:
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


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-540600-01": item(
        city="那曲市",
        name="纳木错北岸景区",
        new_poi_id="verified-xz-naqu-namtso-north-bank",
        category="滨水景观",
        tags=["自然", "滨水", "高原", "摄影"],
        duration=180,
        description="纳木错北岸景区位于那曲市班戈县一侧，以高原湖泊、湖岸地貌和圣象天门等景观为主要体验，适合高原湖景观光与摄影。景区海拔高、受季节和天气影响明显，实际开放与预约安排应以当地公告为准。",
        source_urls=[
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/wldt/202110/t20211025_266836.html",
            "https://wlt.xizang.gov.cn/zwgk_69/xygl/lyjq/202010/t20201015_179169.html",
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/wldt/202504/t20250418_473862.html",
        ],
    ),
    "demo-540600-03": item(
        city="那曲市",
        name="羌塘国家级自然保护区",
        new_poi_id="verified-xz-naqu-qiangtang-reserve",
        category="生态动植物",
        tags=["自然", "生态", "高原", "摄影", "研学"],
        duration=240,
        description="羌塘国家级自然保护区位于西藏北部，主要保护高寒生态系统、湖泊草原和藏羚羊等野生动物，是观察高原自然景观与生态保护成果的区域性目的地。进入保护区应遵守保护管理规定，不把保护区当作可自由穿行的普通景点。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90654.html",
            "https://www.xizang.gov.cn/xwzx_406/ztzl_416/gdzt/jsstwmxgd/mtjj_4437/202204/t20220419_294361.html",
            "https://wsb.xizang.gov.cn/xzjx/lyjd/201107/t20110713_100957.html",
        ],
    ),
    "demo-540600-04": item(
        city="那曲市",
        name="比如骷髅墙",
        new_poi_id="verified-xz-naqu-biru-skeleton-wall",
        category="历史文化",
        tags=["历史文化", "人文", "宗教文化", "摄影"],
        duration=90,
        description="比如骷髅墙位于那曲市比如县唐蕃古道文化旅游景区（多多卡天葬台）内，是当地天葬传统和藏地生死观念的特殊文化遗存。参观应提前预约并遵守当地禁拍、肃静等规定，不对遗存作猎奇化描述。",
        source_urls=[
            "https://nq.xzdw.gov.cn/jrnq/202104/t20210427_183432.html",
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/dsdt/202111/t20211111_269611.html",
            "https://wlt.xizang.gov.cn/xccx/lytg/202102/t20210210_192967.html",
        ],
    ),
    "demo-540600-05": item(
        city="那曲市",
        name="那曲高寒草原",
        new_poi_id="verified-xz-naqu-alpine-grassland",
        category="自然山水",
        tags=["自然", "草原", "高原", "摄影"],
        duration=150,
        description="那曲高寒草原位于藏北高原，以开阔草原、湖泊和高原牧业景观为主要体验，夏秋季适合观景、摄影和了解藏北草原文化。草原属于广域自然景观，具体观景点和通行条件应结合当日天气与当地管理安排。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90636.html",
            "https://wlt.xizang.gov.cn/xccx/lytg/202009/t20200929_177725.html",
            "https://www.xizang.gov.cn/xwzx_406/dsdt/202509/t20250907_498859.html",
        ],
    ),
    "demo-542500-01": item(
        city="阿里地区",
        name="古格王国都城遗址",
        new_poi_id="verified-xz-ali-guge-kingdom-ruins",
        category="历史古迹",
        tags=["历史文化", "古建", "人文", "宗教文化", "摄影"],
        duration=180,
        description="古格王国都城遗址位于阿里地区札达县，保留王宫、寺庙、洞窟和壁画等遗存，是认识古格文明与阿里历史文化的重要遗址，适合在文物保护和开放管理要求下参观。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90614.html",
            "https://drc.xizang.gov.cn/xwzx/daod/202205/t20220524_299929.html",
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/dsdt/202109/t20210903_258600.html",
        ],
    ),
    "demo-542500-03": item(
        city="阿里地区",
        name="班公湖",
        new_poi_id="verified-xz-ali-pangong-lake",
        category="滨水景观",
        tags=["自然", "滨水", "高原", "摄影"],
        duration=150,
        description="班公湖位于阿里地区西部，是高原湖泊与荒漠山地相结合的自然景观，适合湖岸观景、摄影和生态观察。湖区位置偏远、气候变化快，行程应按道路、边境管理和当日安全提示安排。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90614.html",
            "https://wlt.xizang.gov.cn/xccx/lytg/202104/t20210423_200455.html",
            "https://swt.xizang.gov.cn/xxgk/zzqwj/202505/W020250512593477495771.pdf",
        ],
    ),
    "demo-542500-04": item(
        city="阿里地区",
        name="玛旁雍措",
        new_poi_id="verified-xz-ali-mapam-yumco",
        category="滨水景观",
        tags=["自然", "滨水", "高原", "摄影", "宗教文化"],
        duration=180,
        description="玛旁雍措位于阿里地区普兰县，是以圣湖、雪山和高原湖岸为特色的自然与宗教文化景观，适合湖畔观景和摄影。高海拔环境及季节性道路、天气变化会影响游览，具体以当地开放与安全信息为准。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90614.html",
            "https://wlt.xizang.gov.cn/xwzx_69/wlyw/dsdt/202109/t20210903_258600.html",
            "https://wlt.xizang.gov.cn/xccx/lytg/202104/t20210423_200455.html",
        ],
    ),
    "demo-542500-05": item(
        city="阿里地区",
        name="冈仁波齐峰",
        new_poi_id="verified-xz-ali-kailash",
        category="自然山水",
        tags=["自然", "山地", "高原", "摄影", "宗教文化"],
        duration=180,
        description="冈仁波齐峰位于阿里地区普兰县，是冈底斯山脉的重要高峰和多种宗教传统中的神山，主要体验为远观雪山、沿线摄影和高原文化感受。登山、转山及道路通行受天气、海拔和管理要求影响，应按当地规定安排。",
        source_urls=[
            "https://zrzyt.xizang.gov.cn/dt/xzzrzygk/201907/t20190731_90614.html",
            "https://wlt.xizang.gov.cn/xccx/lytg/202104/t20210423_200455.html",
            "https://swt.xizang.gov.cn/xxgk/zzqwj/202505/W020250512593477495771.pdf",
        ],
    ),
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"tibet-verified-batch-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"tibet-verified-batch-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        target_ids = tuple(VERIFIED)
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
        mismatches = []
        accepted_name_aliases = {
            "demo-540600-05": {"那曲草原观景区", "那曲高寒草原"},
            "demo-542500-01": {"古格王朝遗址", "古格王国遗址", "古格王国都城遗址"},
            "demo-542500-04": {"玛旁雍错", "玛旁雍措", "玛旁雍措景区"},
            "demo-542500-05": {"冈仁波齐景区", "冈仁波齐", "冈仁波齐峰"},
        }
        for poi_id, spec in VERIFIED.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name not in accepted_name_aliases.get(poi_id, {spec["name"]}):
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
            return {
                "province": PROVINCE,
                "mode": "preview",
                "verified_replacements": len(VERIFIED),
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
                    "reason": "public-source-verified-tibet-batch",
                    "rows": [_payload(row) for row in affected],
                    "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()},
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
        index.collection.delete(ids=list(VERIFIED))
        vectors_deleted = len(VERIFIED)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {
        "province": PROVINCE,
        "mode": "commit",
        "verified_replacements": len(VERIFIED),
        "affected": preview,
        "backup_path": str(backup_path),
        "vectors_deleted": vectors_deleted,
        "vector_error": vector_error,
        "message": "西藏公开网页核验记录已写入；未调用高德，未新增数据库字段。",
    }


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

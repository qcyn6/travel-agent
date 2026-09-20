"""在不调用高德的前提下清洗云南景点目录。

命令只读本地 MySQL 景点表、项目已有的云南核验备份和本地知识清单，
不会请求任何地图/POI API，也不会向 attractions 表增加字段。

默认是预览；传入 ``--commit`` 才会更新已有的分类、标签、简介和游览时长
四类规划字段。重复、办事处等高风险记录只输出复核清单，不在本命令中删除。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.metadata import infer_interest_tags  # noqa: E402
from backend.attractions.quality import (  # noqa: E402
    description_origin,
    infer_data_source,
    is_non_attraction_name,
    public_space_kind,
    quality_flags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.tag_vocabulary import canonicalize_tags  # noqa: E402
from scripts.enrich_yunnan_local_knowledge import LOCAL_KNOWLEDGE  # noqa: E402


YUNNAN = "云南省"


# 这些是目录中可以仅凭名称确定的分类纠正；不涉及营业时间、票价或坐标。
_CATEGORY_OVERRIDES: dict[str, str] = {
    "B0HKOONUVN": "主题娱乐",  # 小人国主题公园
    "B0FFLIDNEI": "休闲度假",  # 昆明融创滇池后海
    "B0368005Z0": "自然山水",  # 大理苍山世界地质公园
    "B0FFGJWJA3": "休闲度假",  # 理想邦
    "B0FFIPJP95": "主题娱乐",  # 大理海洋世界
    "B0MAMU6I2H": "民俗文化",  # 大理篝火打跳广场
    "B0368000B7": "宗教建筑",  # 小普陀
    "B0368002PY": "宗教建筑",  # 将军洞
    "B036800B74": "历史古迹",  # 南诏德化碑
    "B0LG319HTF": "古镇与街区",  # 珠江源古镇西河坊内的错误“滨海”标签
    "mct-5a-35075": "自然山水",  # 玉龙雪山景区
    "mct-4a-161": "自然山水",  # 观音峡景区
    "mct-4a-2177": "自然山水",  # 蝴蝶泉公园
    "mct-4a-3303": "综合景区",  # 东巴谷旅游景区
    "mct-4a-2349": "民俗文化",  # 玉水寨风景区
    "mct-4a-3062": "自然山水",  # 黎明景区
    "B02B30LWEF": "古镇与街区",  # 告庄西双景
    "mct-4a-1054": "古镇与街区",  # 告庄西双景景区
    "B02B3006LK": "生态动植物",  # 中国科学院西双版纳热带植物园
    "B02B4008ET": "宗教建筑",  # 勐焕大金塔
    "B02B4009OZ": "生态动植物",  # 勐巴娜西珍奇园
    "B0GRL7N28G": "古镇与街区",  # 傣族古镇
    "B0H2L76OAR": "民俗文化",  # 老姆登村景区
    "B03770LV82": "古镇与街区",  # 独克宗古城
    "B0FFM9TXGI": "自然山水",  # 虎跳峡
    "B03770LPV8": "宗教建筑",  # 松赞林景区
    "B03770LV8B": "自然山水",  # 巴拉格宗
    "B036E145MP": "古镇与街区",  # 彝人古镇
    "B036E1464J": "主题娱乐",  # 世界恐龙谷
    "B036E00FI5": "民俗文化",  # 中国彝族十月太阳历文化园
    "B036E0041Y": "古镇与街区",  # 黑井古镇
    "B0373019RH": "古镇与街区",  # 鲁史古镇
    "B036D00DO5": "滨水景观",  # 抚仙湖公园
    "B036D0MLEA": "生态动植物",  # 月亮湾湿地公园
    "B0HA45T638": "滨水景观",  # 红沙滩（抚仙湖岸线）
    "B0FFHU7ULC": "自然山水",  # 普者黑风景区
    "B036C00A0Z": "生态动植物",  # 普者黑喀斯特地貌国家湿地公园
    "B036C0219J": "自然山水",  # 广南坝美风景区
    "B0I6B7OXKV": "古镇与街区",  # 文山七都古镇
    "B0FFGG3R3X": "古镇与街区",  # 建水古城
    "B0FFHCBPBP": "生态动植物",  # 普洱太阳河森林公园
    "B0FFITHK66": "古镇与街区",  # 糯干古寨
    "B0LKYC5681": "历史古迹",  # 普洱茶马古道遗址公园
}


# 只保留明确的公开资料事实，不写营业时间、票价和实时状态。
_PUBLIC_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "B0FFFZH0RQ": {
        "category": "古镇与街区",
        "tags": ("古城", "历史文化", "人文", "白族文化", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "大理古城位于苍山脚下，是南诏国和大理国历史文化空间的代表。古城街巷、城楼、白族建筑与餐饮生活相互交织，适合步行漫游、建筑观察和城市摄影；规划时应按古城片区安排连续游览，而不是把单个城门当作完整景区。",
    },
    "mct-5a-35076": {
        "category": "古镇与街区",
        "tags": ("古城", "古镇", "历史文化", "纳西文化", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "丽江古城是滇西北纳西历史文化与传统街巷格局的代表，水系、石桥、木构建筑和四方街共同构成连续的步行体验。适合历史文化、民族风情、摄影和夜间漫游；建议按街巷片区预留半天左右，并以当天人流和活动安排为准。",
    },
    "mct-5a-35075": {
        "category": "自然山水",
        "tags": ("自然", "雪山", "冰雪", "高原", "民族文化", "摄影", "徒步"),
        "duration": 360,
        "description": "玉龙雪山景区以连续雪峰、高原冰川、蓝月谷和纳西族神山文化构成综合自然目的地。景区内不同景点之间存在接驳和海拔变化，适合自然观赏、摄影和轻户外；规划时应为交通、排队、天气和高原适应预留半天以上。",
    },
    "mct-5a-35077": {
        "category": "宗教建筑",
        "tags": ("历史文化", "人文", "宗教文化", "古建", "建筑", "摄影"),
        "duration": 180,
        "description": "崇圣寺三塔文化旅游区以南诏、大理国时期的佛教塔群和寺院遗存为核心，是大理古城西侧的重要历史文化景区。适合古建筑、宗教文化、历史研学和摄影；建议与大理古城安排在同一日，但保留独立的参观时段。",
    },
    "mct-5a-35078": {
        "category": "生态动植物",
        "tags": ("自然", "森林", "园林", "植物", "研学", "亲子", "摄影"),
        "duration": 300,
        "description": "中国科学院西双版纳热带植物园以热带植物收集、科普教育和园林景观为主要内容，是西双版纳适合自然观察与研学的整块目的地。园区面积和步行距离较大，适合亲子、植物爱好者和摄影安排半天以上。",
    },
    "mct-5a-35079": {
        "category": "生态动植物",
        "tags": ("自然", "森林", "高原", "山地", "生态", "摄影", "徒步"),
        "duration": 360,
        "description": "普达措国家公园以高原湖泊、森林、草甸和山地生态系统为主要体验，适合自然观赏、摄影和轻徒步。高原天气与步道状态会影响节奏，规划时应预留半天以上并根据实时开放信息调整线路。",
    },
    "mct-5a-35080": {
        "category": "古典园林",
        "tags": ("园林", "花卉", "休闲", "亲子", "研学", "摄影"),
        "duration": 240,
        "description": "昆明世界园艺博览园以园林展园、植物景观和花卉展示为主要内容，适合城市休闲、亲子、研学和摄影。园区空间较大，建议根据重点展园选择线路并预留半天左右。",
    },
    "mct-5a-35081": {
        "category": "休闲度假",
        "tags": ("自然", "温泉", "地质景观", "休闲", "摄影"),
        "duration": 240,
        "description": "腾冲火山热海旅游区把火山地质景观、地热泉群和温泉休闲结合在一起，适合自然观察、地质研学与放松休闲。火山与热海属于不同体验段，建议按景区线路安排半天，并以当天开放信息为准。",
    },
    "mct-5a-35082": {
        "category": "自然山水",
        "tags": ("自然", "喀斯特", "湖泊", "湿地", "民族文化", "摄影", "乡村"),
        "duration": 300,
        "description": "普者黑旅游景区以喀斯特峰丛、湖泊湿地、村落和彝族文化景观构成区域性自然目的地。水面、陆地步道和村寨体验需要组合安排，适合自然观赏、摄影和乡村慢游，建议预留半天以上。",
    },
    "mct-5a-35083": {
        "category": "古镇与街区",
        "tags": ("古镇", "历史文化", "侨乡", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "和顺古镇是腾冲侨乡文化、马帮文化与传统民居建筑相互交融的历史村镇，街巷、祠堂、图书馆和水系适合连续步行浏览。适合历史文化、建筑观察和慢游，建议预留半天，并尊重古镇居民的日常生活。",
    },
    "demo-533100-03": {
        "category": "滨水景观",
        "tags": ("自然", "江河", "民族文化", "边境风情", "摄影", "自驾"),
        "duration": 240,
        "description": "瑞丽江—大盈江国家级风景名胜区由多个片区和沿江景点组成，兼具河谷、边境口岸和傣族、景颇族等民族文化景观。它是区域性风景廊道，不宜按单一小景点安排；适合沿线观景、自驾和摄影，具体停留点应结合当天线路。",
    },
    "demo-533100-04": {
        "category": "古镇与街区",
        "tags": ("边境风情", "跨境文化", "商业街", "民族文化", "美食", "城市漫游"),
        "duration": 120,
        "description": "姐告边境贸易区是瑞丽面向缅甸的开放式边境旅游空间，兼具口岸、边贸、珠宝玉石和民族风情体验。它适合安排半日城市漫游和边境文化观察；通行、口岸管理和开放范围以出行当天的官方信息为准。",
    },
    "demo-533300-01": {
        "category": "自然山水",
        "tags": ("自然", "峡谷", "江河", "山地", "民族文化", "摄影", "自驾"),
        "duration": 300,
        "description": "怒江大峡谷是由江河深切峡谷、碧罗雪山与高黎贡山山地、村落和多民族文化共同构成的连续旅行廊道。它不适合按一个城市景点计算时长，规划时应按县市、观景台和村寨拆分，并为山路和天气留出弹性。",
    },
    "demo-533300-03": {
        "category": "自然山水",
        "tags": ("自然", "峡谷", "江河", "民族文化", "徒步", "摄影", "乡村"),
        "duration": 300,
        "description": "独龙江生态体验区依托独龙江乡的峡谷河流、森林、村落和独龙族文化，适合生态观察、徒步和民族文化体验。它是区域性目的地，交通与季节性天气会显著影响节奏，规划时应按完整线路预留时间。",
    },
    "demo-533300-05": {
        "category": "民俗文化",
        "tags": ("民族文化", "民俗", "乡村", "山地", "摄影", "休闲"),
        "duration": 240,
        "description": "老姆登村位于碧罗雪山半山腰，是具有浓郁怒族风情的特色村寨，老姆登教堂、民族歌舞、火塘和山地峡谷视野共同构成主要体验。适合乡村慢游、民族文化和摄影，建议与知子罗、沙瓦等周边点位组合安排。",
    },
}


# 仅用于审计报告，不会写入 attractions 表。名称来自当前云南目录中已确认
# 的同景区、父子景点或行政办事处记录。
_OVERLAP_GROUPS: tuple[tuple[str, ...], ...] = (
    ("石林风景区", "昆明市石林风景名胜区"),
    ("世界园艺博览园", "昆明市昆明世界园艺博览园景区"),
    ("官渡古镇", "云南省昆明市官渡区官渡古镇"),
    ("九乡风景区", "云南省昆明市宜良县九乡风景区"),
    ("西山风景区", "云南省昆明市西山区西山风景区"),
    ("丽江古城", "丽江市丽江古城景区"),
    ("玉龙雪山国家级风景名胜区", "丽江市玉龙雪山景区"),
    ("束河古镇", "云南省丽江市古城区束河古镇景区"),
    ("泸沽湖", "云南省丽江市宁蒗彝族自治县泸沽湖景区"),
    ("东巴谷景区", "云南省丽江市玉龙纳西族自治县东巴谷旅游景区"),
    ("大理古城", "云南省大理白族自治州大理市大理古城景区"),
    ("崇圣寺三塔文化旅游区", "大理州崇圣寺三塔文化旅游区"),
    ("南诏风情岛", "云南省大理白族自治州大理市南诏风情岛景区"),
    ("蝴蝶泉", "云南省大理白族自治州大理市喜洲镇蝴蝶泉公园"),
    ("普者黑风景区", "文山州普者黑旅游景区"),
    ("中国科学院西双版纳热带植物园", "西双版纳州中国科学院西双版纳热带植物园景区"),
    ("告庄西双景", "云南省西双版纳傣族自治州景洪市告庄西双景景区"),
    ("恐龙谷", "云南省楚雄彝族自治州禄丰县恐龙谷景区"),
    ("老姆登村", "老姆登村景区"),
    ("丽江虎跳峡景区", "云南省迪庆藏族自治州香格里拉市虎跳峡景区"),
    ("香格里拉虎跳峡景区", "云南省迪庆藏族自治州香格里拉市虎跳峡景区"),
)


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-catalog-before-clean-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-catalog-before-clean-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _load_verified_reference(path: Path) -> dict[str, dict[str, Any]]:
    """读取旧的云南公开资料快照；快照缺失时仍可执行本地规则。"""

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("rows", []):
        if not row.get("poi_id"):
            continue
        # 这个快照只用于规划字段；坐标和城市归属留在当前数据库中。
        result[str(row["poi_id"])] = {
            "category": row.get("category"),
            "tags": tuple(row.get("tags") or ()),
            "description": row.get("description") or "",
            "duration": row.get("visit_duration_minutes"),
        }
    return result


def _knowledge_by_poi() -> dict[str, dict[str, Any]]:
    return {
        item.poi_id: {
            "category": item.category,
            "tags": item.tags,
            "description": item.description,
            "duration": item.visit_duration_minutes,
        }
        for item in LOCAL_KNOWLEDGE
    }


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _row_mapping(row: AttractionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "poi_id": row.poi_id,
        "name": row.name,
        "category": row.category,
        "tags": list(row.tags or []),
        "description": row.description,
        "longitude": row.longitude,
        "latitude": row.latitude,
        "visit_duration_minutes": row.visit_duration_minutes,
    }


def _normalise_tags(values: Any) -> list[str]:
    return canonicalize_tags(values or (), fallback="人文")


def _overlap_candidates(rows: list[AttractionRow]) -> list[list[dict[str, Any]]]:
    by_normalised = defaultdict(list)
    for row in rows:
        by_normalised[normalize_attraction_name(row.name)].append(row)
    result: list[list[dict[str, Any]]] = []
    for aliases in _OVERLAP_GROUPS:
        selected: list[AttractionRow] = []
        alias_keys = {normalize_attraction_name(name) for name in aliases}
        for key in alias_keys:
            selected.extend(by_normalised.get(key, []))
        # Prefixes such as “云南省大理州……” make exact matching too strict.
        if not selected:
            for row in rows:
                value = normalize_attraction_name(row.name)
                if any(normalize_attraction_name(alias) in value or value in normalize_attraction_name(alias) for alias in aliases):
                    selected.append(row)
        unique = {row.id: row for row in selected}
        if len(unique) > 1:
            result.append([
                {
                    "id": row.id,
                    "poi_id": row.poi_id,
                    "name": row.name,
                    "category": row.category,
                    "source": infer_data_source(row.poi_id),
                }
                for row in sorted(unique.values(), key=lambda item: item.id)
            ])
    return result


def _noise_candidates(rows: list[AttractionRow]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        flags = quality_flags(_row_mapping(row))
        if is_non_attraction_name(row.name) or "non_attraction_name" in flags:
            result.append({
                "id": row.id,
                "poi_id": row.poi_id,
                "name": row.name,
                "category": row.category,
                "reason": "非景点服务/办事处名称",
            })
    return result


def _public_space_candidates(rows: list[AttractionRow]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        kind = public_space_kind(row.name, row.category)
        if kind in {"urban_park", "urban_square", "facility_square"}:
            result.append({
                "id": row.id,
                "poi_id": row.poi_id,
                "name": row.name,
                "category": row.category,
                "kind": kind,
            })
    return result


def clean(
    *,
    commit: bool,
    backup_dir: Path,
    reference_path: Path,
) -> dict[str, Any]:
    init_database()
    reference = _load_verified_reference(reference_path)
    local = _knowledge_by_poi()
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == YUNNAN)
                .order_by(AttractionRow.id)
            ).all()
        )
        rows = [row for row, _city in pairs]
        changes: list[dict[str, Any]] = []
        before_rows: dict[int, dict[str, Any]] = {}
        counters = Counter()
        reference_rows = 0
        local_rows = 0
        public_rows = 0

        for row, city in pairs:
            # Capture the complete row before mutating the ORM object.  The
            # rollback snapshot must represent the actual pre-clean state,
            # including coordinates and every column currently in the schema.
            before_rows[row.id] = _payload(row)
            before = {
                "category": row.category,
                "tags": list(row.tags or []),
                "description": row.description,
                "visit_duration_minutes": row.visit_duration_minutes,
            }
            update: dict[str, Any] = {}

            if row.poi_id in reference:
                update.update(reference[row.poi_id])
                reference_rows += 1
                counters["reference"] += 1
            if row.poi_id in local:
                update.update(local[row.poi_id])
                local_rows += 1
                counters["local_knowledge"] += 1
            if row.poi_id in _PUBLIC_KNOWLEDGE:
                update.update(_PUBLIC_KNOWLEDGE[row.poi_id])
                public_rows += 1
                counters["public_knowledge"] += 1

            category = _CATEGORY_OVERRIDES.get(row.poi_id) or update.get("category") or row.category
            tags = _normalise_tags(update.get("tags") if "tags" in update else row.tags)
            if row.poi_id in _CATEGORY_OVERRIDES and row.poi_id not in local and row.poi_id not in _PUBLIC_KNOWLEDGE:
                tags = _normalise_tags(infer_interest_tags(row.name, category))
            description = update.get("description") or row.description
            duration = update.get("duration") or row.visit_duration_minutes

            # 公开资料快照/本地知识中的描述优先；其余 derived 描述暂不强行
            # 改写，避免再次生成没有事实依据的“真实简介”。
            after = {
                "category": category,
                "tags": tags,
                "description": description,
                "visit_duration_minutes": int(duration or 90),
            }
            if before != after:
                changes.append({
                    "id": row.id,
                    "poi_id": row.poi_id,
                    "city": city.name,
                    "name": row.name,
                    "before": before,
                    "after": after,
                })
                for field in before:
                    if before[field] != after[field]:
                        counters[f"changed_{field}"] += 1
            if commit and before != after:
                row.category = after["category"]
                row.tags = after["tags"]
                row.description = after["description"]
                row.visit_duration_minutes = after["visit_duration_minutes"]

        backup_path = None
        if commit and changes:
            backup_path = _backup_path(backup_dir)
            backup_path.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "province": YUNNAN,
                        "reason": "yunnan-catalog-safe-clean",
                        "rows": [before_rows[change["id"]] for change in changes],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            session.commit()

        noise = _noise_candidates(rows)
        public_spaces = _public_space_candidates(rows)
        overlaps = _overlap_candidates(rows)
        origins = Counter(description_origin(row.description) for row in rows)
        source_counts = Counter(infer_data_source(row.poi_id) for row in rows)
        return {
            "province": YUNNAN,
            "rows": len(rows),
            "mode": "commit" if commit else "preview",
            "reference_file": str(reference_path),
            "reference_rows": reference_rows,
            "local_knowledge_rows": local_rows,
            "public_knowledge_rows": public_rows,
            "changed_rows": len(changes),
            "changed_fields": {key.removeprefix("changed_"): value for key, value in counters.items() if key.startswith("changed_")},
            "description_origins_after": dict(origins),
            "data_source_inferred": dict(source_counts),
            "noise_candidates": noise,
            "public_space_candidates": public_spaces,
            "overlap_groups": overlaps,
            "backup_path": str(backup_path) if backup_path else None,
            "preview": changes[:40],
            "message": "仅更新已有字段；非景点、普通公园和父子重复未自动删除，请依据清单单独确认。",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="写入 MySQL；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    parser.add_argument(
        "--reference-file",
        type=Path,
        default=ROOT / "data" / "backups" / "yunnan-before-unified-demo-20260917-234658.json",
        help="已有的云南公开资料快照；文件不存在时仍可按本地清单运行",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir, reference_path=args.reference_file), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

"""Audit province/city catalog integrity and description provenance.

The audit deliberately does not mutate the database.  It compares the rows
stored in MySQL with the application's canonical administrative catalog and
reports issues that need public-source verification.  The province/city
schema is kept unchanged: no provenance columns are added.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Allow `python scripts/audit_destination_catalog.py` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database.catalog import CATALOG
from backend.database.session import open_session
from backend.database.tables import CityRow, ProvinceRow
from backend.tag_vocabulary import CANONICAL_TAGS, canonicalize_tags

def _catalog_maps() -> tuple[dict[str, dict], dict[str, tuple[str, str, list[str], str]]]:
    provinces: dict[str, dict] = {}
    cities: dict[str, tuple[str, str, list[str], str]] = {}
    for province in CATALOG:
        provinces[province["adcode"]] = province
        for name, adcode, tags, description in province["cities"]:
            cities[adcode] = (province["name"], name, list(tags), description)
    return provinces, cities


def _normalise_text(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _description_profile(description: str | None) -> str:
    text = _normalise_text(description)
    if not text:
        return "empty"
    if len(text) < 18:
        return "too_short"
    if "适合结合" in text and text.endswith("安排行程。"):
        return "template_suffix"
    if "适合" in text and text.endswith("。"):
        return "catalog_copy"
    return "free_text"


_PLACEHOLDER_MARKERS = ("待补充", "示例", "演示", "待核验", "待复核", "暂无", "测试", "mock", "template")


def audit() -> dict:
    catalog_provinces, catalog_cities = _catalog_maps()
    canonical = set(CANONICAL_TAGS)

    with open_session() as session:
        provinces = list(session.scalars(select(ProvinceRow).order_by(ProvinceRow.adcode)))
        cities = list(
            session.scalars(
                select(CityRow).options(selectinload(CityRow.province)).order_by(CityRow.adcode)
            )
        )

    province_by_code = {row.adcode: row for row in provinces}
    province_by_name = {row.name: row for row in provinces}
    city_by_code = {row.adcode: row for row in cities}
    city_by_name = {row.name: row for row in cities}

    duplicate_province_names = [name for name, n in Counter(row.name for row in provinces).items() if n > 1]
    duplicate_province_codes = [code for code, n in Counter(row.adcode for row in provinces).items() if n > 1]
    duplicate_city_names = [name for name, n in Counter(row.name for row in cities).items() if n > 1]
    duplicate_city_codes = [code for code, n in Counter(row.adcode for row in cities).items() if n > 1]

    province_missing = sorted(set(catalog_provinces) - set(province_by_code))
    province_extra = sorted(set(province_by_code) - set(catalog_provinces))
    province_mismatch: list[dict] = []
    for code in sorted(set(catalog_provinces) & set(province_by_code)):
        expected = catalog_provinces[code]
        actual = province_by_code[code]
        if actual.name != expected["name"]:
            province_mismatch.append({"adcode": code, "expected_name": expected["name"], "actual_name": actual.name})

    city_missing = sorted(set(catalog_cities) - set(city_by_code))
    city_extra = sorted(set(city_by_code) - set(catalog_cities))
    city_mismatch: list[dict] = []
    city_orphan: list[dict] = []
    province_code_format = [
        {"name": row.name, "adcode": row.adcode}
        for row in provinces
        if not re.fullmatch(r"\d{6}", row.adcode or "") or not row.adcode.endswith("0000")
    ]
    city_code_format = [
        {"name": row.name, "adcode": row.adcode}
        for row in cities
        if not re.fullmatch(r"\d{6}", row.adcode or "")
    ]
    city_parent_code_mismatch: list[dict] = []
    province_ids = {row.id for row in provinces}
    for code in sorted(set(catalog_cities) & set(city_by_code)):
        expected_province, expected_name, _, _ = catalog_cities[code]
        actual = city_by_code[code]
        actual_province = actual.province.name if actual.province else None
        if actual.name != expected_name or actual_province != expected_province:
            city_mismatch.append(
                {
                    "adcode": code,
                    "expected_province": expected_province,
                    "expected_name": expected_name,
                    "actual_province": actual_province,
                    "actual_name": actual.name,
                }
            )
        expected_province_code = next(
            (province_code for province_code, province in catalog_provinces.items() if province["name"] == expected_province),
            None,
        )
        if expected_province_code and not code.startswith(expected_province_code[:2]):
            city_parent_code_mismatch.append(
                {"adcode": code, "name": actual.name, "expected_province": expected_province, "expected_prefix": expected_province_code[:2]}
            )
        if actual.province_id not in province_ids:
            city_orphan.append({"adcode": code, "name": actual.name, "province_id": actual.province_id})

    province_tags_bad: dict[str, list[str]] = {}
    city_tags_bad: dict[str, list[str]] = {}
    province_descriptions: Counter[str] = Counter()
    city_descriptions: Counter[str] = Counter()
    for row in provinces:
        bad = sorted(set(row.tags or []) - canonical)
        if bad:
            province_tags_bad[row.adcode] = bad
        province_descriptions[_description_profile(row.description)] += 1
    for row in cities:
        bad = sorted(set(row.tags or []) - canonical)
        if bad:
            city_tags_bad[row.adcode] = bad
        city_descriptions[_description_profile(row.description)] += 1

    city_template_rows = [
        {"adcode": row.adcode, "province": row.province.name if row.province else None, "name": row.name, "description": row.description}
        for row in cities
        if _description_profile(row.description) == "template_suffix"
    ]
    province_template_rows = [
        {"adcode": row.adcode, "name": row.name, "description": row.description}
        for row in provinces
        if _description_profile(row.description) == "template_suffix"
    ]
    city_duplicate_descriptions = [
        {"description": description, "count": count}
        for description, count in Counter(_normalise_text(row.description) for row in cities).most_common()
        if description and count > 1
    ]
    province_placeholder_rows = [
        {"adcode": row.adcode, "name": row.name, "description": row.description}
        for row in provinces
        if any(marker in (row.description or "") for marker in _PLACEHOLDER_MARKERS)
    ]
    city_placeholder_rows = [
        {"adcode": row.adcode, "province": row.province.name if row.province else None, "name": row.name, "description": row.description}
        for row in cities
        if any(marker in (row.description or "") for marker in _PLACEHOLDER_MARKERS)
    ]
    same_name_destination_rows = [
        {"province": row.province.name if row.province else None, "city": row.name, "adcode": row.adcode}
        for row in cities
        if row.province is not None and row.province.name == row.name
    ]

    # Description/tag equality with the canonical catalog is useful for
    # detecting seed drift, but does not by itself prove public-source truth.
    city_catalog_drift: list[dict] = []
    for row in cities:
        expected = catalog_cities.get(row.adcode)
        if expected is None:
            continue
        expected_province, expected_name, expected_tags, expected_description = expected
        expected_canonical_tags = list(canonicalize_tags(expected_tags))
        if row.name != expected_name or list(row.tags or []) != expected_canonical_tags or _normalise_text(row.description) != _normalise_text(expected_description):
            city_catalog_drift.append({
                "adcode": row.adcode,
                "name": row.name,
                "expected_name": expected_name,
                "tags_match": list(row.tags or []) == expected_canonical_tags,
                "description_match": _normalise_text(row.description) == _normalise_text(expected_description),
            })

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {
            "province_rows": len(provinces),
            "city_rows": len(cities),
            "catalog_provinces": len(catalog_provinces),
            "catalog_cities": len(catalog_cities),
        },
        "administrative_integrity": {
            "province_missing_adcodes": province_missing,
            "province_extra_adcodes": province_extra,
            "province_name_mismatch": province_mismatch,
            "province_duplicate_names": sorted(duplicate_province_names),
            "province_duplicate_adcodes": sorted(duplicate_province_codes),
            "province_code_format_errors": province_code_format,
            "city_missing_adcodes": city_missing,
            "city_extra_adcodes": city_extra,
            "city_name_or_parent_mismatch": city_mismatch,
            "city_orphan_rows": city_orphan,
            "city_duplicate_names": sorted(duplicate_city_names),
            "city_duplicate_adcodes": sorted(duplicate_city_codes),
            "city_code_format_errors": city_code_format,
            "city_parent_code_mismatch": city_parent_code_mismatch,
        },
        "content_integrity": {
            "canonical_tag_count": len(canonical),
            "province_unknown_tags": province_tags_bad,
            "city_unknown_tags": city_tags_bad,
            "province_description_profiles": dict(province_descriptions),
            "city_description_profiles": dict(city_descriptions),
            "province_template_rows": province_template_rows,
            "province_placeholder_rows": province_placeholder_rows,
            "city_template_row_count": len(city_template_rows),
            "city_template_rows": city_template_rows,
            "city_placeholder_rows": city_placeholder_rows,
            "same_name_destination_rows": same_name_destination_rows,
            "city_duplicate_descriptions": city_duplicate_descriptions,
            "city_catalog_drift": city_catalog_drift,
        },
        "interpretation": {
            "administrative_names_and_adcodes": "structurally_verified_against_backend_catalog",
            "tags_and_descriptions": "catalog_text_requires_public_source_spot_check",
            "no_database_mutation": True,
        },
    }


def write_report(result: dict, path: Path) -> None:
    admin = result["administrative_integrity"]
    content = result["content_integrity"]
    scope = result["scope"]
    lines = [
        "# 省份与城市数据真实性审计（当前）",
        "",
        f"> 审计时间：{result['generated_at']}。本次只读审计，没有修改数据库，也没有调用高德。",
        "",
        "## 1. 审计范围",
        "",
        f"- 数据库：{scope['province_rows']} 个省级条目、{scope['city_rows']} 个城市条目。",
        f"- 应用行政目录：{scope['catalog_provinces']} 个省级条目、{scope['catalog_cities']} 个城市条目。",
        "- 行政名称/代码与项目目录比对；标签和描述单独统计，不把本地模板当作外部事实证明。",
        "",
        "## 2. 行政名称与代码",
        "",
        f"- 省级缺失：{len(admin['province_missing_adcodes'])}；多余：{len(admin['province_extra_adcodes'])}；名称不一致：{len(admin['province_name_mismatch'])}；代码格式错误：{len(admin['province_code_format_errors'])}。",
        f"- 城市缺失：{len(admin['city_missing_adcodes'])}；多余：{len(admin['city_extra_adcodes'])}；名称/隶属不一致：{len(admin['city_name_or_parent_mismatch'])}；代码格式错误：{len(admin['city_code_format_errors'])}；省级前缀不一致：{len(admin['city_parent_code_mismatch'])}。",
        f"- 孤立城市行：{len(admin['city_orphan_rows'])}；省/城市重复名称或代码：{len(admin['province_duplicate_names']) + len(admin['province_duplicate_adcodes']) + len(admin['city_duplicate_names']) + len(admin['city_duplicate_adcodes'])} 项。",
        "",
        "结论：当前省市行与项目行政目录在数量、代码、名称和父子关系上应当一致；这只能证明结构完整，不能证明画像文案来自公开资料。",
        "",
        "## 3. 标签与描述字段",
        "",
        f"- 省级未知标签：{len(content['province_unknown_tags'])} 行；城市未知标签：{len(content['city_unknown_tags'])} 行。",
        f"- 省级描述类型：{content['province_description_profiles']}。",
        f"- 城市描述类型：{content['city_description_profiles']}。",
        f"- 省级占位/演示描述：{len(content['province_placeholder_rows'])} 行；城市占位/演示描述：{len(content['city_placeholder_rows'])} 行。",
        f"- 城市描述重复文本：{len(content['city_duplicate_descriptions'])} 组；城市与本地目录漂移：{len(content['city_catalog_drift'])} 行。",
        f"- 省名与城市名相同的直辖/特别行政区行：{len(content['same_name_destination_rows'])} 行；这是行政建模现象，不按重复数据删除。",
        "",
        "注意：城市画像大量由 `city_profiles.py` 的 `_profile()` 生成，常见句式为“……适合结合……安排行程”。这属于产品画像文案，不等同于逐条经过文旅官网核验的事实描述。",
        "",
        "省级描述中有少量篇幅较短的概括句；这不是行政数据错误，但若要达到景点级的真实性标准，应使用省政府/文旅厅公开概况重新撰写。",
        "",
        "## 4. 需要继续核验的真实性边界",
        "",
        "1. 行政名称和代码：可用民政部/省级民政部门公开行政区划名录做外部复核；国家统计局已说明县以上代码统一采用国家标准。",
        "2. 省/城市标签：作为检索画像，应以公开文旅资料中的稳定主题为依据；无法找到依据的标签应归并或删除。",
        "3. 省/城市描述：应按省级/城市文旅官网、政府概况页逐条替换为事实型简介；不要新增数据库字段，来源记录放在审计文档或脚本日志。",
        "4. 台湾省：当前行政目录只有省级条目，城市覆盖需要单独确认产品范围，不应因缺少城市就伪造城市画像。",
        "",
        "## 5. 本次外部抽样依据",
        "",
        "- [中国政府网：中华人民共和国行政区划](https://www.locpg.gov.cn/2022-06/07/c_1211652713.htm)：34 个省级行政区的结构说明。",
        "- [司法部：行政区划代码管理办法](https://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzbmgz/202512/t20251204_528920.html)：省级、地级行政区划代码的管理主体和更新规则。",
        "- [北京市人民政府：走进北京](https://www.beijing.gov.cn/renwen/)：北京历史文化、胡同、博物馆等画像主题抽样核对。",
        "- [大理州人民政府：苍山、洱海和大理古城资料](https://www.dali.gov.cn/dlzrmzf/c101724/pc/content/1968887167324360704/content_1968887167324360704.html)：云南城市/自然主题抽样核对。",
        "",
        "## 6. 机器审计原始结果",
        "",
        f"完整 JSON：`{path.with_suffix('.json').as_posix()}`。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print JSON instead of writing the report")
    args = parser.parse_args()
    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "省市数据真实性审计-当前.md"
    json_path = report_path.with_suffix(".json")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(result, report_path)
    print(report_path)
    print(json_path)
    print(json.dumps({"scope": result["scope"], "administrative_integrity": result["administrative_integrity"], "content_profiles": result["content_integrity"]["city_description_profiles"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

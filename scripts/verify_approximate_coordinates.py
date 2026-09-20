"""Verify approximate catalog coordinates with Amap text search.

The catalog previously filled missing coordinates with city anchors and stable
offsets.  This command classifies that provenance and replaces only high-
confidence exact-name matches from Amap. Preview is the default; ``--commit``
writes a complete backup before changing rows.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.observability import observe_external_request  # noqa: E402


_ADMIN_PREFIX = re.compile(
    r"^(?:中华人民共和国|中国|[\u4e00-\u9fff]{2,8}(?:省|市|自治区|特别行政区|自治州|地区|盟))"
)
_GENERIC_WORDS = re.compile(
    r"国家重点|国家级|风景名胜区|风景名胜|旅游景区|旅游区|风景区|景区|景点|度假区"
)
_NAME_SPLIT = re.compile(r"[—\-·•/,，、()（）\[\]【】\s]+")


def _location(value) -> tuple[float, float] | None:
    if not isinstance(value, str) or "," not in value:
        return None
    try:
        longitude, latitude = (float(part.strip()) for part in value.split(",", 1))
    except (TypeError, ValueError):
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _name_variants(name: str, city: str, province: str) -> set[str]:
    raw = str(name or "").strip()
    values = {normalize_attraction_name(raw), normalize_attraction_name(_GENERIC_WORDS.sub("", raw))}
    for prefix in (province, city):
        if prefix:
            values.add(normalize_attraction_name(raw.removeprefix(prefix)))
    values.add(normalize_attraction_name(_ADMIN_PREFIX.sub("", raw)))
    for part in _NAME_SPLIT.split(raw):
        core = normalize_attraction_name(_GENERIC_WORDS.sub("", part))
        if len(core) >= 3:
            values.add(core)
    return {value for value in values if len(value) >= 3}


def _match_poi(pois: list[object], *, name: str, city: str, province: str):
    variants = _name_variants(name, city, province)
    exact = []
    for raw in pois:
        if not isinstance(raw, dict):
            continue
        candidate_name = str(raw.get("name") or "").strip()
        location = _location(raw.get("location"))
        if not candidate_name or location is None:
            continue
        candidate_variants = _name_variants(candidate_name, city, province)
        if variants.intersection(candidate_variants):
            exact.append((candidate_name, location, raw))
    if not exact:
        # Long government/catalog names often have a shorter provider display
        # name (for example “杭州市西湖风景名胜区” -> “西湖”). Accept a
        # unique containment match only when the shared core is at least three
        # Chinese characters, keeping the city-limited query as a guardrail.
        fuzzy = []
        for raw in pois:
            if not isinstance(raw, dict):
                continue
            candidate_name = str(raw.get("name") or "").strip()
            location = _location(raw.get("location"))
            if not candidate_name or location is None:
                continue
            candidate_variants = _name_variants(candidate_name, city, province)
            shared = [
                min(len(left), len(right))
                for left in variants
                for right in candidate_variants
                if left in right or right in left
            ]
            if shared:
                fuzzy.append((max(shared), candidate_name, location, raw))
        if not fuzzy:
            return None
        fuzzy.sort(key=lambda item: (-item[0], item[1] != name, -len(item[1])))
        if len(fuzzy) > 1 and fuzzy[0][0] == fuzzy[1][0] and fuzzy[0][1] != fuzzy[1][1]:
            return None
        return fuzzy[0][1], fuzzy[0][2], fuzzy[0][3]
    # Prefer the same display name; otherwise choose the longest matching name
    # because it is less likely to be a similarly named nearby POI.
    exact.sort(key=lambda item: (item[0] != name, -len(item[0])))
    return exact[0]


async def _resolve_rows(
    rows,
    *,
    concurrency: int,
    retries: int,
    delay_ms: int,
    timeout_seconds: float,
):
    settings = get_settings()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    resolved: dict[str, dict] = {}
    failures: list[dict] = []

    async with httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        timeout=timeout_seconds,
    ) as client:
        async def resolve(row, city: str, province: str) -> None:
            async with semaphore:
                last_error: Exception | None = None
                for attempt in range(retries + 1):
                    try:
                        params = {
                            "key": settings.amap_api_key,
                            "keywords": _GENERIC_WORDS.sub("", _ADMIN_PREFIX.sub("", row.name)).strip() or row.name,
                            "region": row.city.adcode,
                            "city_limit": "true",
                            "page_size": 20,
                            "page_num": 1,
                            "show_fields": "business",
                        }
                        async with observe_external_request("amap", "coordinate_verify"):
                            response = await client.get("/v5/place/text", params=params)
                        response.raise_for_status()
                        payload = response.json()
                        if payload.get("status") != "1":
                            raise RuntimeError(str(payload.get("info") or "高德查询失败"))
                        match = _match_poi(
                            payload.get("pois") or [],
                            name=row.name,
                            city=city,
                            province=province,
                        )
                        if match is None:
                            raise LookupError("未找到同名且带坐标的高德 POI")
                        candidate_name, location, raw = match
                        resolved[row.poi_id] = {
                            "longitude": location[0],
                            "latitude": location[1],
                            "provider_name": candidate_name,
                            "provider_id": str(raw.get("id") or ""),
                        }
                        return
                    except Exception as exc:  # noqa: BLE001 - keep the batch running
                        last_error = exc
                        if attempt < retries:
                            await asyncio.sleep(0.5 * (attempt + 1))
                failures.append(
                    {
                        "poi_id": row.poi_id,
                        "city": city,
                        "name": row.name,
                        "error": type(last_error).__name__ if last_error else "UnknownError",
                        "message": str(last_error) if last_error else "未知错误",
                    }
                )
                if delay_ms:
                    await asyncio.sleep(delay_ms / 1000)

        await asyncio.gather(
            *(resolve(row, city.name, province.name) for row, city, province in rows)
        )
    return resolved, failures


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"attraction-coordinate-verify-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"attraction-coordinate-verify-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _load_history_missing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("poi_id"))
        for row in payload.get("tables", {}).get("attractions", [])
        if row.get("longitude") is None or row.get("latitude") is None
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 条；0 表示全部")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay-ms", type=int, default=120)
    parser.add_argument("--timeout-seconds", type=float, default=4.0)
    parser.add_argument(
        "--history-backup",
        type=Path,
        default=ROOT / "data" / "backups" / "attraction-metadata-before-enrichment-20260916-103929.json",
    )
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if (
        args.limit < 0
        or args.concurrency < 1
        or args.retries < 0
        or args.delay_ms < 0
        or args.timeout_seconds <= 0
    ):
        parser.error("limit/concurrency/retries/delay-ms 参数无效")
    return args


async def verify(*, args) -> dict:
    init_database()
    history_missing = _load_history_missing(args.history_backup)
    with open_session() as session:
        joined = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(AttractionRow.is_active.is_(True))
                .order_by(AttractionRow.id)
            ).all()
        )
        source_by_id: dict[int, str] = {}
        for row, _city, _province in joined:
            source = row.coordinate_source
            if source == "unknown":
                if row.poi_id in history_missing or row.data_source == "curated":
                    source = "approximate_catalog"
                elif row.data_source == "amap":
                    source = "amap_import"
                elif row.data_source == "demo":
                    source = "demo"
                else:
                    source = "catalog_import"
            source_by_id[row.id] = source
        approximate = [
            item
            for item in joined
            if source_by_id[item[0].id] == "approximate_catalog"
        ]
        if args.limit:
            approximate = approximate[: args.limit]
        rows = [(row, city, province) for row, city, province in approximate]
        resolved, failures = await _resolve_rows(
            rows,
            concurrency=args.concurrency,
            retries=args.retries,
            delay_ms=args.delay_ms,
            timeout_seconds=args.timeout_seconds,
        )
        backup_path = None
        if args.commit:
            backup_path = _backup_path(args.backup_dir)
            backup_path.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "rows": [
                            {column.name: getattr(row, column.name) for column in row.__table__.columns}
                            for row in session.scalars(select(AttractionRow).order_by(AttractionRow.id))
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            approximate_ids = {
                row.poi_id
                for row, _city, _province in joined
                if source_by_id[row.id] == "approximate_catalog"
            }
            for row, _city, _province in joined:
                if row.poi_id in resolved:
                    row.longitude = resolved[row.poi_id]["longitude"]
                    row.latitude = resolved[row.poi_id]["latitude"]
                    row.coordinate_source = "amap_verified"
                else:
                    row.coordinate_source = source_by_id[row.id]
            session.commit()
        else:
            for row, _city, _province in joined:
                if row.poi_id in resolved:
                    row.coordinate_source = "amap_verified"
                else:
                    row.coordinate_source = source_by_id[row.id]
        return {
            "active_rows": len(joined),
            "approximate_selected": len(approximate),
            "resolved": len(resolved),
            "failed": len(failures),
            "committed": args.commit,
            "remaining_approximate": len(approximate) - len(resolved),
            "backup_path": str(backup_path) if backup_path else None,
            "failures": failures[:100],
        }


def main() -> None:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(asyncio.run(verify(args=args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

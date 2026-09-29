"""Replace city profile prose with bounded public encyclopedia summaries.

The script is conservative: it requires an exact city-title/administrative
context match, changes only ``cities.description``, and records the source in
an audit cache.  Administrative names, adcodes, tags, and the database schema
are not changed.  It never calls Amap.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import html
import json
from pathlib import Path
import re
import sys
import threading
from urllib.parse import quote

import requests
from sqlalchemy import select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import CityRow  # noqa: E402


API_URL = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
WIKI_API_URL = "https://zh.wikipedia.org/w/api.php"
USER_AGENT = "travel-agent-public-source-audit/1.0"
CACHE_PATH = ROOT / "data" / "destination_sources" / "city_baike_cache.json"
OVERRIDE_PATH = ROOT / "backend" / "database" / "public_city_profiles.py"
BACKUP_DIR = ROOT / "data" / "backups"
MIN_TEXT_LENGTH = 24
MAX_TEXT_LENGTH = 280
_thread_local = threading.local()


def _session() -> requests.Session:
    value = getattr(_thread_local, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
        _thread_local.session = value
    return value


def _clean(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    value = re.sub(r"\[[0-9]+\]", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip()


def _normalise(value: str) -> str:
    return re.sub(r"[\s\-—－·•/｜|（）()【】\[\]，,。:：]", "", value).lower()


def _city_stems(name: str) -> list[str]:
    value = name
    stems = [value]
    for suffix in ("特别行政区", "自治州", "地区", "林区", "盟", "市", "县", "区"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            stems.append(value[: -len(suffix)])
            break
    return list(dict.fromkeys(stems))


def _stable_summary(abstract: str, desc: str, name: str) -> str | None:
    source = _clean(abstract) or _clean(desc)
    if not source:
        return None
    source = re.split(r"(?:截至|目前|其中|202[0-9]年|20[0-9]{2}年|人口|总?面积)", source, maxsplit=1)[0]
    source = re.sub(r"（[^（）]*[A-Za-z][^（）]*）", "", source)
    source = source.replace("英文名，", "")
    source = re.sub(r"[，、；]?(?:全市陆域|全市土地|全市|陆域|陆地|海域|土地|总)$", "", source)
    source = source.strip("，、；。总 ")
    if source.endswith(name) and len(source) > len(name) + MIN_TEXT_LENGTH:
        source = source[: -len(name)].rstrip("，、；。 ")
    if len(source) < MIN_TEXT_LENGTH:
        return None
    if len(source) > MAX_TEXT_LENGTH:
        prefix = source[:MAX_TEXT_LENGTH]
        boundary = max(prefix.rfind(mark) for mark in ("。", "！", "？", "；"))
        source = prefix[: boundary + 1] if boundary >= MIN_TEXT_LENGTH else prefix.rstrip("，、；") + "…"
    if not source.endswith(("。", "！", "？", "…")):
        source += "。"
    stems = [_normalise(item) for item in _city_stems(name)]
    if not any(stem and stem in _normalise(source) for stem in stems):
        return None
    return source


def _fetch(item: tuple[str, str, str]) -> tuple[str, dict | None]:
    adcode, province, city = item
    try:
        response = _session().get(API_URL, params={"appid": "379020", "bk_key": city}, timeout=20)
        if response.status_code != 200:
            return adcode, None
        body = response.json()
    except (requests.RequestException, ValueError):
        return adcode, None
    title = _clean(str(body.get("title") or body.get("key") or city))
    abstract = _clean(str(body.get("abstract") or ""))
    desc = _clean(str(body.get("desc") or ""))
    description = _stable_summary(abstract, desc, city)
    if not description:
        return adcode, None
    title_norm = _normalise(title)
    city_norm = _normalise(city)
    if not (title_norm == city_norm or city_norm in title_norm or title_norm in city_norm):
        return adcode, None
    # Require the parent administrative context for ambiguous short names.
    province_stem = _normalise(province)
    context = _normalise(abstract)
    if len(city_norm) < 3 and province_stem not in context:
        return adcode, None
    source_url = str(body.get("url") or body.get("wapUrl") or "")
    if not source_url:
        source_url = f"https://baike.baidu.com/item/{quote(title)}"
    return adcode, {
        "name": city,
        "province": province,
        "title": title,
        "source_url": source_url,
        "abstract": abstract,
        "description": description,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _fetch_wikipedia(item: tuple[str, str, str]) -> tuple[str, dict | None]:
    """Fallback for city pages absent from the Baidu open lemma endpoint."""
    adcode, province, city = item
    try:
        response = _session().get(
            WIKI_API_URL,
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "redirects": 1,
                "format": "json",
                "titles": city,
            },
            timeout=20,
        )
        if response.status_code != 200:
            return adcode, None
        pages = response.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError):
        return adcode, None
    page = next((value for value in pages.values() if value.get("extract")), None)
    if not page:
        return adcode, None
    title = _clean(str(page.get("title") or city))
    title_norm = _normalise(title)
    city_norm = _normalise(city)
    if not (title_norm == city_norm or city_norm in title_norm or title_norm in city_norm):
        return adcode, None
    abstract = _clean(str(page.get("extract") or ""))
    description = _stable_summary(abstract, "", city)
    if not description:
        return adcode, None
    return adcode, {
        "name": city,
        "province": province,
        "title": title,
        "source_url": f"https://zh.wikipedia.org/wiki/{quote(title)}",
        "abstract": abstract,
        "description": description,
        "source_type": "wikipedia",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_override(matched: list[dict], existing: dict[tuple[str, str], tuple[str, str]] | None = None) -> None:
    values = dict(existing or {})
    for item in matched:
        values[(item["province"], item["name"])] = (item["title"], item["description"])
    lines = [
        '"""Public-source city profile prose overrides. Generated by refresh script."""',
        "",
        "from typing import Final",
        "",
        "PUBLIC_CITY_PROFILES: Final[dict[tuple[str, str], tuple[str, str]]] = {",
    ]
    for (province, city), value in sorted(values.items()):
        lines.append(f"    ({province!r}, {city!r}): ({value[0]!r}, {value[1]!r}),")
    lines.extend(["}", ""])
    OVERRIDE_PATH.write_text("\n".join(lines), encoding="utf-8")


def _backup_path() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"city-profiles-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"


def run(*, commit: bool, refresh: bool, province_filter: str | None, workers: int, wikipedia_fallback: bool) -> dict:
    init_database()
    with open_session() as session:
        rows = list(
            session.scalars(
                select(CityRow).options(selectinload(CityRow.province)).order_by(CityRow.adcode)
            )
        )
        if province_filter:
            rows = [row for row in rows if row.province and row.province.name == province_filter]
        before = {
            row.adcode: {"name": row.name, "province": row.province.name if row.province else None, "tags": list(row.tags or []), "description": row.description}
            for row in rows
        }

    cache = {} if refresh else _load_cache()
    pending = [
        (row.adcode, row.province.name if row.province else "", row.name)
        for row in rows
        if refresh or row.adcode not in cache or (wikipedia_fallback and not (cache.get(row.adcode) or {}).get("result"))
    ]
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
            def fetch_item(item):
                adcode, result = _fetch(item)
                if result is None and wikipedia_fallback:
                    return _fetch_wikipedia(item)
                return adcode, result

            futures = [pool.submit(fetch_item, item) for item in pending]
            for future in as_completed(futures):
                adcode, result = future.result()
                cache[adcode] = {"result": result}
    _save_cache(cache)

    matched: list[dict] = []
    unresolved: list[dict] = []
    for row in rows:
        payload = cache.get(row.adcode) or {}
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("description"):
            matched.append({
                "adcode": row.adcode,
                "name": row.name,
                "province": row.province.name if row.province else "",
                "title": result["title"],
                "source_url": result["source_url"],
                "description": result["description"],
                "before": row.description,
                "tags": list(row.tags or []),
            })
        else:
            unresolved.append({"adcode": row.adcode, "name": row.name, "province": row.province.name if row.province else ""})

    if not commit:
        return {"mode": "preview", "rows": len(rows), "matched": len(matched), "unresolved": unresolved, "cache_path": str(CACHE_PATH), "sample": matched[:20]}

    backup_path = _backup_path()
    backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "rows": list(before.values())}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with open_session() as session:
        db_rows = {row.adcode: row for row in session.scalars(select(CityRow).where(CityRow.adcode.in_([item["adcode"] for item in matched])))}
        for item in matched:
            db_rows[item["adcode"]].description = item["description"]
        session.commit()
    existing: dict[tuple[str, str], tuple[str, str]] = {}
    if OVERRIDE_PATH.exists():
        # Keep previously committed overrides without importing stale catalog code.
        namespace: dict = {}
        exec(OVERRIDE_PATH.read_text(encoding="utf-8"), namespace)
        existing = dict(namespace.get("PUBLIC_CITY_PROFILES") or {})
    _write_override(matched, existing)
    return {"mode": "commit", "rows": len(rows), "updated": len(matched), "unresolved": unresolved, "backup_path": str(backup_path), "cache_path": str(CACHE_PATH), "override_path": str(OVERRIDE_PATH), "sources": [{"province": item["province"], "name": item["name"], "title": item["title"], "url": item["source_url"]} for item in matched]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--province", help="只处理一个省级名称，例如 云南省")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--wikipedia-fallback", action="store_true", help="百度词条缺失时使用中文维基摘要兜底")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(run(commit=args.commit, refresh=args.refresh_cache, province_filter=args.province, workers=args.workers, wikipedia_fallback=args.wikipedia_fallback), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Fill remaining template descriptions from exact Chinese Wikipedia entries.

This is a second public-source pass after the Baidu Baike pass.  It intentionally
uses exact/bounded title matching and leaves ambiguous child POIs untouched.
Only ``attractions.description`` is changed; no Amap calls or schema changes.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

import requests
import urllib3
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402

API = "https://zh.wikipedia.org/w/api.php"
CACHE_PATH = ROOT / "data" / "description_sources" / "wikipedia_cache.json"
UA = "travel-agent-public-source-audit/1.0"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _norm(value: str) -> str:
    return re.sub(r"[\s\-—－·•/｜|（）()【】\[\]，,。:：]", "", value).lower()


def _clean(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    value = re.sub(r"\s+", "", value).replace("……", "…")
    value = re.sub(r"\[[0-9]+\]", "", value)
    return value.strip()


def _clip(value: str, limit: int = 560) -> str:
    value = _clean(value)
    if len(value) <= limit:
        return value
    prefix = value[:limit]
    boundary = max(prefix.rfind(x) for x in ("。", "！", "？"))
    return prefix[: boundary + 1] if boundary >= 28 else prefix.rstrip("，、；") + "…"


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        value = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(value: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _context(province: str, city: str) -> list[str]:
    out = []
    for value in (province, city):
        for suffix in ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市"):
            if value.endswith(suffix) and len(value) > len(suffix) + 1:
                value = value[: -len(suffix)]
                break
        if len(value) >= 2:
            out.append(value)
    return out


def _relevant(page: dict, name: str, province: str, city: str) -> bool:
    title = _norm(str(page.get("title") or ""))
    original = _norm(name)
    if not title or not original:
        return False
    title_match = title == original or title in original or original in title
    if not title_match:
        return False
    # Do not copy a parent page to a child named with a suffix.
    for sep in ("-", "—", "－", "·", "/", "｜", "|"):
        if sep in name:
            child = _norm(name.split(sep, 1)[1])
            if child and child not in title and child not in _norm(str(page.get("extract") or "")):
                return False
            break
    extract = str(page.get("extract") or "")
    if any(token in extract or token in str(page.get("title") or "") for token in _context(province, city)):
        return True
    return len(original) >= 6 and title == original


def _batch(session: requests.Session, names: list[str]) -> dict[str, dict | None]:
    if not names:
        return {}
    try:
        body = session.get(
            API,
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "redirects": 1,
                "format": "json",
                "titles": "|".join(names),
            },
            timeout=15,
            verify=False,
        ).json()
    except (requests.RequestException, ValueError):
        return {name: None for name in names}
    pages = body.get("query", {}).get("pages", {})
    by_title = {str(page.get("title") or ""): page for page in pages.values()}
    result = {}
    for name in names:
        page = by_title.get(name)
        if not page:
            # redirects may change title; use normalized exact name only here.
            page = next((item for item in by_title.values() if _norm(str(item.get("title") or "")) == _norm(name)), None)
        if page and _clean(str(page.get("extract") or "")):
            title = str(page.get("title") or name)
            result[name] = {
                "title": title,
                "abstract": _clip(str(page.get("extract") or "")),
                "source_url": f"https://zh.wikipedia.org/wiki/{quote(title)}",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        else:
            result[name] = None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_database()
    with open_session() as session:
        rows = list(
            session.execute(
                select(AttractionRow, CityRow, ProvinceRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(AttractionRow.description.like("%归入“%"))
                .order_by(AttractionRow.id)
            ).all()
        )
    cache = {} if args.refresh_cache else _load_cache()
    unique_names = sorted({row.name for row, _city, _province in rows if args.refresh_cache or row.name not in cache})
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for start in range(0, len(unique_names), 10):
        cache.update(_batch(s, unique_names[start : start + 10]))
    _save_cache(cache)
    matched = []
    unresolved = []
    for row, city, province in rows:
        page = cache.get(row.name)
        if isinstance(page, dict) and page.get("abstract") and _relevant(page, row.name, province.name, city.name):
            matched.append((row, city, province, page))
        else:
            unresolved.append({"poi_id": row.poi_id, "name": row.name, "city": city.name, "province": province.name})
    if not args.commit:
        print(json.dumps({"mode": "preview", "template_records": len(rows), "matched": len(matched), "unresolved": len(unresolved), "sample": [{"name": r.name, "source_title": p["title"], "after": p["abstract"]} for r, _c, _pr, p in matched[:10]], "unresolved_sample": unresolved[:20], "cache_path": str(CACHE_PATH)}, ensure_ascii=False, indent=2))
        return
    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"wikipedia-descriptions-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open_session() as session:
        db_rows = list(session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_([r.poi_id for r, _c, _pr, _p in matched]))))
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "source": "Chinese Wikipedia extracts", "rows": [{column.name: getattr(row, column.name) for column in row.__table__.columns} for row in db_rows]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        by_id = {row.poi_id: row for row in db_rows}
        for row, _city, _province, page in matched:
            by_id[row.poi_id].description = page["abstract"]
        session.commit()
    vectors_deleted = 0
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=[row.poi_id for row, _c, _pr, _p in matched])
        vectors_deleted = len(matched)
    except Exception:
        pass
    print(json.dumps({"mode": "commit", "updated": len(matched), "unresolved": len(unresolved), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "cache_path": str(CACHE_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

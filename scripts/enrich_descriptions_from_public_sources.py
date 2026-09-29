"""Use public encyclopedia entries to replace remaining template attraction descriptions.

This script deliberately changes only ``attractions.description``.  It does not
call Amap and it does not change the database schema.  The Baidu Baike open
lemma-card endpoint is used as a public reference source; a local JSON cache is
written so the selected source title/URL and fetched abstract can be audited.

Default mode fetches and previews candidates.  ``--commit`` writes only rows
whose current description is still the old generated template.  Rows that do
not have a sufficiently relevant public entry are left unchanged and reported.
After a commit, refresh the local Chroma vectors with
``rebuild_all_vectors.py --commit`` (or the province rebuild script).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import html
import json
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


API_URL = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
SEARCH_URL = "https://baike.baidu.com/search/word"
USER_AGENT = "travel-agent-public-source-audit/1.0"
TEMPLATE_MARKER = "归入“"
MIN_ABSTRACT_LENGTH = 28
MAX_DESCRIPTION_LENGTH = 560
CACHE_PATH = ROOT / "data" / "description_sources" / "baike_cache.json"

_thread_local = threading.local()


def _session() -> requests.Session:
    value = getattr(_thread_local, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
        _thread_local.session = value
    return value


def _normalized(value: str) -> str:
    return re.sub(r"[\s\-—－·•/｜|（）()【】\[\]，,。:：·]", "", value).lower()


def _query_variants(name: str) -> list[str]:
    """Generate conservative aliases; no fuzzy city/POI invention."""
    values = [name.strip()]
    for separator in ("-", "—", "－", "·", "/", "｜", "|"):
        if separator in name:
            values.append(name.split(separator, 1)[0].strip())
            # Child POIs such as “西湖风景名胜区-曲院风荷” have their own
            # encyclopedia entries; try that child token as well.
            values.append(name.split(separator, 1)[1].strip())
    suffixes = (
        "国家级旅游景区",
        "国家旅游度假区",
        "旅游度假区",
        "文化旅游区",
        "历史文化特色街区",
        "历史文化街区",
        "风景名胜区",
        "旅游景区",
        "自然保护区",
        "风景区",
        "旅游区",
        "景区",
        "公园",
    )
    for value in list(values):
        for suffix in suffixes:
            if value.endswith(suffix) and len(value) > len(suffix) + 2:
                values.append(value[: -len(suffix)])
    # Keep only meaningful distinct values and avoid very short generic queries.
    result: list[str] = []
    for value in values:
        value = value.strip()
        if len(value) >= 2 and value not in result:
            result.append(value)
    return result


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", "", value)
    value = value.replace("……", "…")
    # Baike abstracts occasionally include citation marker text.
    value = re.sub(r"\[[0-9]+\]", "", value)
    return value.strip()


def _clip_abstract(value: str) -> str:
    text = _clean_html(value)
    if len(text) <= MAX_DESCRIPTION_LENGTH:
        return text
    # Prefer a complete first few sentences instead of cutting a fact in half.
    prefix = text[:MAX_DESCRIPTION_LENGTH]
    ends = [prefix.rfind(mark) for mark in ("。", "！", "？")]
    boundary = max(ends)
    if boundary >= MIN_ABSTRACT_LENGTH:
        return prefix[: boundary + 1]
    return prefix.rstrip("，、；") + "…"


def _fetch_api(query: str) -> dict[str, Any] | None:
    try:
        response = _session().get(
            API_URL,
            params={"appid": "379020", "bk_key": query},
            timeout=18,
        )
        if response.status_code != 200:
            return None
        body = response.json()
    except (requests.RequestException, ValueError):
        return None
    abstract = _clip_abstract(str(body.get("abstract") or ""))
    desc = _clean_html(str(body.get("desc") or ""))
    if len(abstract) < MIN_ABSTRACT_LENGTH and len(desc) < MIN_ABSTRACT_LENGTH:
        return None
    title = _clean_html(str(body.get("title") or body.get("key") or query))
    source_url = str(body.get("url") or body.get("wapUrl") or "")
    return {
        "query": query,
        "title": title,
        "key": _clean_html(str(body.get("key") or query)),
        "abstract": abstract,
        "desc": desc,
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _canonical_from_redirect(query: str) -> str | None:
    """Search endpoint often redirects aliases to the canonical lemma title.

    Even when Baidu returns a security page, the final redirect URL contains the
    canonical title for legitimate aliases (for example 径山寺 -> 万寿禅寺).
    """
    try:
        response = _session().get(
            SEARCH_URL,
            params={"word": query},
            timeout=18,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None
    parsed = urlparse(response.url)
    path = parsed.path
    if "/item/" not in path:
        return None
    tail = path.split("/item/", 1)[1].strip("/")
    if not tail:
        return None
    encoded_title = tail.split("/", 1)[0]
    title = unquote(encoded_title).strip()
    # Do not treat a generic error page as a canonical lemma.
    if len(title) < 2 or title.lower() in {"error", "search"}:
        return None
    return title


def _context_tokens(province: str, city: str) -> list[str]:
    values = [province, city]
    out: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        for suffix in ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市"):
            if value.endswith(suffix) and len(value) > len(suffix) + 1:
                value = value[: -len(suffix)]
                break
        if len(value) >= 2:
            out.append(value)
    return out


def _is_relevant(candidate: dict[str, Any], name: str, province: str, city: str, query: str) -> bool:
    title = str(candidate.get("title") or "")
    key = str(candidate.get("key") or "")
    abstract = str(candidate.get("abstract") or "")
    normalized_name = _normalized(name)
    normalized_title = _normalized(title)
    normalized_key = _normalized(key)
    normalized_query = _normalized(query)
    # Exact lemma or a canonical alias is preferred.  A title containing the
    # original POI name is also safe for entries such as “某景区景区”.
    title_match = (
        normalized_name == normalized_title
        or normalized_name == normalized_key
        or normalized_query == normalized_title
        or normalized_query == normalized_key
        or normalized_name in normalized_title
        or normalized_title in normalized_name
        or (len(normalized_query) >= 3 and normalized_query in normalized_title)
    )
    if not title_match:
        return False
    # A suffix-stripped query such as “安昌” can otherwise resolve to the
    # administrative “安昌街道” page instead of the attraction “安昌古镇”.
    if "古镇" in name and "古镇" not in title and "古镇" not in _normalized(abstract):
        return False
    if any(word in title for word in ("街道", "社区", "村委会")) and any(word in name for word in ("景区", "公园", "古镇", "风景")):
        return False
    # A parent prefix is not evidence for a named child POI.  For example,
    # “西湖风景名胜区-曲院风荷” must not inherit the whole West Lake entry just
    # because the prefix variant resolved; require the child token to appear.
    for separator in ("-", "—", "－", "·", "/", "｜", "|"):
        if separator in name:
            child = name.split(separator, 1)[1].strip()
            child_norm = _normalized(child)
            if child_norm and child_norm not in normalized_title and child_norm not in _normalized(abstract):
                return False
            break
    context = _context_tokens(province, city)
    if any(token in abstract or token in title for token in context):
        return True
    # For distinctive long names, an exact lemma is generally unambiguous even
    # when the short abstract omits the administrative location.
    return len(normalized_name) >= 6 and normalized_name == normalized_title


def _lookup(row_data: tuple[str, str, str, str]) -> tuple[str, dict[str, Any] | None]:
    poi_id, name, province, city = row_data
    queries = _query_variants(name)
    for prefix in (city, province):
        if name.startswith(prefix) and len(name) > len(prefix) + 1:
            queries.append(name[len(prefix) :].strip())
    queries = list(dict.fromkeys(query for query in queries if len(query) >= 2))
    for query in queries:
        candidate = _fetch_api(query)
        if candidate and _is_relevant(candidate, name, province, city, query):
            candidate["match_type"] = "lemma"
            return poi_id, candidate
    # Resolve aliases that do not have a direct lemma key.
    for query in queries[:3]:
        canonical = _canonical_from_redirect(query)
        if canonical and canonical not in _query_variants(name):
            candidate = _fetch_api(canonical)
            if candidate and _is_relevant(candidate, name, province, city, canonical):
                candidate["match_type"] = "redirect_alias"
                candidate["alias_query"] = query
                return poi_id, candidate
    return poi_id, None


def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        body = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="仅调试前 N 条；默认全部模板简介")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--retry-unresolved", action="store_true", help="只重新查询缓存中未命中的条目")
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
                .where(AttractionRow.description.like(f"%{TEMPLATE_MARKER}%"))
                .order_by(AttractionRow.id)
            ).all()
        )
    if args.limit > 0:
        rows = rows[: args.limit]
    cache = {} if args.refresh_cache else _load_cache()
    tasks: list[tuple[str, str, str, str]] = []
    for row, city, province in rows:
        cached = cache.get(row.poi_id)
        # ``None`` is a deliberate negative-cache result.  Repeating thousands
        # of failed public lookups on every commit would be both slow and
        # unfriendly to the source; ``--refresh-cache`` is the explicit opt-in
        # for another attempt.
        cache_valid = cached is None and row.poi_id in cache and not args.retry_unresolved
        if isinstance(cached, dict):
            cache_valid = bool(
                cached.get("abstract")
                and _is_relevant(cached, row.name, province.name, city.name, str(cached.get("query") or row.name))
            )
        if not cache_valid:
            tasks.append((row.poi_id, row.name, province.name, city.name))

    started = time.time()
    results: dict[str, dict[str, Any] | None] = {}
    if tasks:
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 20))) as executor:
            futures = {executor.submit(_lookup, task): task[0] for task in tasks}
            for future in as_completed(futures):
                poi_id, candidate = future.result()
                results[poi_id] = candidate
                cache[poi_id] = candidate
        _save_cache(cache)
    else:
        results = {row.poi_id: cache.get(row.poi_id) for row, _city, _province in rows}

    # Cache entries may be null from an earlier run; turn them into result keys.
    for row, _city, _province in rows:
        if row.poi_id not in results:
            results[row.poi_id] = cache.get(row.poi_id)

    matched = []
    unresolved = []
    for row, city, province in rows:
        candidate = results.get(row.poi_id)
        if candidate and candidate.get("abstract"):
            matched.append((row, city, province, candidate))
        else:
            unresolved.append({"poi_id": row.poi_id, "name": row.name, "province": province.name, "city": city.name})

    if not args.commit:
        print(
            json.dumps(
                {
                    "mode": "preview",
                    "template_records": len(rows),
                    "matched": len(matched),
                    "unresolved": len(unresolved),
                    "cache_entries": len(cache),
                    "elapsed_seconds": round(time.time() - started, 1),
                    "sample": [
                        {
                            "poi_id": row.poi_id,
                            "name": row.name,
                            "source_title": candidate.get("title"),
                            "source_url": candidate.get("source_url"),
                            "after": candidate.get("abstract"),
                        }
                        for row, _city, _province, candidate in matched[:10]
                    ],
                    "unresolved_sample": unresolved[:20],
                    "cache_path": str(CACHE_PATH),
                    "message": "仅预览；传入 --commit 才写入。未调用高德。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"public-descriptions-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open_session() as session:
        db_rows = list(
            session.scalars(
                select(AttractionRow).where(AttractionRow.poi_id.in_([item[0].poi_id for item in matched]))
            )
        )
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "Baidu Baike public lemma-card abstracts",
                    "rows": [_payload(row) for row in db_rows],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        by_id = {row.poi_id: row for row in db_rows}
        for row, _city, _province, candidate in matched:
            by_id[row.poi_id].description = candidate["abstract"]
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=[row.poi_id for row, _city, _province, _candidate in matched])
        vectors_deleted = len(matched)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    print(
        json.dumps(
            {
                "mode": "commit",
                "updated": len(matched),
                "unresolved": len(unresolved),
                "backup_path": str(backup_path),
                "vectors_deleted": vectors_deleted,
                "vector_error": vector_error,
                "unresolved_sample": unresolved[:20],
                "cache_path": str(CACHE_PATH),
                "message": "已写入可核验公开词条摘要；未调用高德、未新增字段。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

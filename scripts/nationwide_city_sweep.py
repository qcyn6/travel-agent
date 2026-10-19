"""File-backed nationwide research ledger; never creates or migrates tables.

Export the existing city catalog, combine independent web-research shards,
and optionally insert only individually approved, source-backed attractions.
All provenance stays in JSON files, not in database columns.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import event, func, inspect, select
from backend.database import get_engine, open_session
from backend.database.tables import AttractionRow, CityRow, ProvinceRow
from backend.attractions.dedupe import normalize_attraction_name
from backend.tag_vocabulary import canonicalize_tags

WORK = ROOT / "data" / "city_sweep_20261002"


@event.listens_for(get_engine(), "before_cursor_execute")
def block_schema_mutation(connection, cursor, statement, parameters, context, executemany):
    # Fail before execution, not merely after a potentially unsafe migration.
    verb = statement.lstrip().split(None, 1)[0].upper()
    if verb in {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "UPDATE", "DELETE", "REPLACE"}:
        raise RuntimeError(f"Research maintenance forbids SQL operation: {verb}")


def schema_snapshot():
    inspector = inspect(get_engine())
    return {table: {"columns": [{"name": c["name"], "type": str(c["type"]),
                                "nullable": c["nullable"], "default": c.get("default")}
                               for c in inspector.get_columns(table)],
                    "pk": inspector.get_pk_constraint(table),
                    "fk": inspector.get_foreign_keys(table),
                    "indexes": inspector.get_indexes(table),
                    "unique": inspector.get_unique_constraints(table)}
            for table in sorted(inspector.get_table_names())}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def existing_row_fingerprints():
    with open_session() as session:
        return {str(row.id): hashlib.sha256(json.dumps(
                    {column.name: getattr(row, column.name)
                     for column in AttractionRow.__table__.columns},
                    sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
                for row in session.scalars(select(AttractionRow).order_by(AttractionRow.id))}


def check_existing_rows():
    baseline = WORK / "existing_rows_before.json"
    if not baseline.exists():
        save(baseline, existing_row_fingerprints())
    expected = json.loads(baseline.read_text(encoding="utf-8"))
    current = existing_row_fingerprints()
    if any(current.get(key) != value for key, value in expected.items()):
        raise RuntimeError("A pre-existing attraction row changed; refusing maintenance")
    return len(expected)


def export():
    with open_session() as session:
        existing = {}
        for city_id, name in session.execute(select(AttractionRow.city_id, AttractionRow.name)):
            existing.setdefault(city_id, []).append(name)
        cities = [{"id": c.id, "city": c.name, "province": p.name,
                   "existing": existing.get(c.id, [])}
                  for c, p in session.execute(select(CityRow, ProvinceRow)
                    .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                    .order_by(ProvinceRow.id, CityRow.id))]
    if not (WORK / "schema_before.json").exists():
        save(WORK / "schema_before.json", schema_snapshot())
    save(WORK / "catalog.json", cities)
    for shard in range(4):
        save(WORK / f"catalog_{shard}.json", cities[shard::4])
    return {"cities": len(cities), "attractions": sum(len(c["existing"]) for c in cities),
            "shards": [len(cities[i::4]) for i in range(4)]}


def load_reviews():
    reviews = {}
    for path in sorted(WORK.glob("review_*.json")):
        for review in json.loads(path.read_text(encoding="utf-8")):
            city_id = review["id"]
            if city_id in reviews:
                raise ValueError(f"Duplicate city review: {city_id}")
            if not review.get("sources") or not review.get("checked_names"):
                raise ValueError(f"Review lacks source or checked names: {city_id}")
            reviews[city_id] = review
    return reviews


def report():
    cities = json.loads((WORK / "catalog.json").read_text(encoding="utf-8"))
    reviews = load_reviews()
    valid = {c["id"] for c in cities}
    if set(reviews) - valid:
        raise ValueError("Review references cities outside the existing catalog")
    before = json.loads((WORK / "schema_before.json").read_text(encoding="utf-8"))
    unchanged = before == schema_snapshot()
    candidates = [item for r in reviews.values() for item in r.get("candidates", [])]
    approved = sum(item.get("status") == "approved" for item in candidates)
    excluded = sum(str(item.get("status", "")).startswith("excluded_") for item in candidates)
    pending_count = len(candidates) - approved - excluded
    audits = [json.loads(path.read_text(encoding="utf-8")) for path in WORK.glob("import_*.json")]
    inserted = sum(audit.get("added", 0) for audit in audits if audit.get("committed"))
    with open_session() as session:
        database_count = session.scalar(select(func.count()).select_from(AttractionRow))
    preserved = check_existing_rows()
    lines = ["# 全国逐城市景点核查进度", "", f"更新：{datetime.now().isoformat(timespec='seconds')}", "",
             f"现有城市/地区：{len(cities)}；已有检索核查记录：{len(reviews)}；待核查：{len(cities)-len(reviews)}。", "",
             f"本轮登记候选：{len(candidates)}；资料齐全候选：{approved}；实际新增入库：{inserted}；排除重复或不宜新增候选：{excluded}；尚待补齐资料：{pending_count}。", "",
             f"当前数据库景点：{database_count}；开始前的{preserved}条景点记录内容均未改变。", "",
             "本表的完成表示做过一轮公开资料检索并登记候选，不代表已穷尽该城市所有景点；未核实简介、坐标或独立性的候选不入库。",
             "不调用高德；不新增、删除或迁移数据库表及字段。", "",
             "新增坐标使用具名POI节点、小型场馆建筑或游览入口等公开坐标；建筑锚点不冒充现场测量入口。公开资料WGS84在大陆地区离线转换为项目使用的GCJ02，来源保存在独立JSON。游览时长是规划估计，不冒充官方固定时长。", "",
             f"数据库结构与启动快照一致：{'是' if unchanged else '否（需检查）'}。", "",
             "|省份|城市/地区|核查状态|本轮核查名称|待补资料候选|来源|", "|---|---|---|---|---|---|"]
    for c in cities:
        r = reviews.get(c["id"])
        names = "、".join(r.get("checked_names", [])) if r else "—"
        pending = "、".join(x["name"] for x in r.get("candidates", []) if x.get("status") != "approved" and not str(x.get("status", "")).startswith("excluded_")) if r else "—"
        sources = "；".join(f"[资料{i+1}]({u})" for i,u in enumerate(r["sources"])) if r else "—"
        lines.append(f"|{c['province']}|{c['city']}|{'已检索核查' if r else '待核查'}|{names}|{pending or '—'}|{sources}|")
    extra_path = WORK / "cities_outside_catalog.json"
    if extra_path.exists():
        extras = json.loads(extra_path.read_text(encoding="utf-8"))
        lines.extend(["", "## 现有城市目录之外的覆盖缺口", "",
                      "以下城市已检索到官方景点资料，但当前数据库没有对应城市记录。资料保存在外部清单；不虚构行政区代码，也不把景点错误挂到其他城市。现有372条城市记录及其字段未改动。", "",
                      "|省份|城市|已查到名称|来源|", "|---|---|---|---|"])
        for extra in extras:
            links = "；".join(f"[资料{i+1}]({u})" for i,u in enumerate(extra["sources"]))
            lines.append(f"|{extra['province']}|{extra['city']}|{'、'.join(extra['checked_names'])}|{links}|")
    path = ROOT / "docs" / "全国逐城市景点核查进度-20261002.md"
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return {"reviewed": len(reviews), "total": len(cities), "pending": len(cities)-len(reviews),
            "candidates": len(candidates), "approved": approved, "inserted": inserted,
            "pending_candidates": pending_count, "excluded_candidates": excluded, "database_count": database_count,
            "preexisting_rows_unchanged": preserved,
            "schema_unchanged": unchanged, "report": str(path)}


def import_approved(commit=False):
    from scripts.import_public_attractions import bd09_to_gcj02, wgs84_to_gcj02
    before = schema_snapshot()
    if before != json.loads((WORK / "schema_before.json").read_text(encoding="utf-8")):
        raise RuntimeError("Live schema differs from original snapshot; refusing import")
    preserved_rows = check_existing_rows()
    reviews = load_reviews()
    added, skipped = [], []
    with open_session() as session:
        cities = {c.id: (c,p) for c,p in session.execute(select(CityRow,ProvinceRow)
                   .join(ProvinceRow, ProvinceRow.id == CityRow.province_id))}
        existing = {}
        for row in session.scalars(select(AttractionRow)):
            existing.setdefault(row.city_id, []).append(row)
        for city_id, review in reviews.items():
            city, province = cities[city_id]
            for item in review.get("candidates", []):
                if item.get("status") != "approved":
                    continue
                required = ("name", "description", "description_source_url", "longitude", "latitude",
                            "coordinate_source_url", "coordinate_system", "category", "tags", "duration_minutes", "independence_note")
                if any(item.get(k) in (None, "", []) for k in required):
                    raise ValueError(f"Incomplete verified candidate: {item.get('name')}")
                if not 20 <= len(item["description"]) <= 800:
                    raise ValueError("Description length outside accepted range")
                lon, lat = float(item["longitude"]), float(item["latitude"])
                if not (73 < lon < 136 and 3 < lat < 54):
                    raise ValueError("Coordinate outside national geographic bounds")
                system = str(item["coordinate_system"]).upper().replace("-", "")
                if system == "BD09":
                    lon, lat = bd09_to_gcj02(lon,lat)
                elif system == "WGS84":
                    lon, lat = wgs84_to_gcj02(lon,lat,province=province.name)
                elif system != "GCJ02":
                    raise ValueError("Unknown coordinate system")
                identity = normalize_attraction_name(item["name"])
                aliases = [identity] + [normalize_attraction_name(a) for a in item.get("aliases", [])]
                duplicate = next((row for row in existing.get(city_id, [])
                                  if any(a == normalize_attraction_name(row.name) for a in aliases)), None)
                if duplicate:
                    skipped.append({"city": city.name,"name":item["name"],"reason":"existing name/alias", "existing":duplicate.name})
                    continue
                poi_id = "public_" + hashlib.sha256(f"{province.name}|{city.name}|{identity}".encode()).hexdigest()[:32]
                if session.scalar(select(AttractionRow.id).where(AttractionRow.poi_id == poi_id)):
                    skipped.append({"city":city.name,"name":item["name"],"reason":"existing poi_id"})
                    continue
                row = AttractionRow(poi_id=poi_id, city_id=city_id,name=item["name"],category=item["category"],
                                    tags=canonicalize_tags(item["tags"]),description=item["description"],
                                    longitude=lon,latitude=lat,visit_duration_minutes=int(item["duration_minutes"]))
                if not row.tags or not 15 <= row.visit_duration_minutes <= 600:
                    raise ValueError("Invalid tags or estimated duration")
                if commit:
                    session.add(row)
                existing.setdefault(city_id, []).append(row)
                added.append({**item,"poi_id":poi_id,"city_id":city_id,"city":city.name,"province":province.name,
                              "stored_longitude":lon,"stored_latitude":lat,
                              "duration_note":"用于规划的估计值，不冒充官方固定游览时长"})
        if commit:
            session.commit()
    if before != schema_snapshot():
        raise RuntimeError("Schema changed during research import")
    check_existing_rows()
    payload = {"created_at":datetime.now().isoformat(),"committed":commit,"added":len(added),"rows":added,"skipped":skipped,"schema_unchanged":True,
               "preexisting_rows_unchanged": preserved_rows}
    if commit:
        save(WORK / f"import_{datetime.now().strftime('%H%M%S')}.json",payload)
    return {k:v for k,v in payload.items() if k != "rows"}


def vector_state():
    # Deliberately bypass init_database/create_all/upgrade_schema.
    from backend.config import get_settings
    from backend.knowledge.repository import KnowledgeRepository
    from backend.rag.vector_store import ChromaAttractionIndex, _content_hash
    settings = get_settings()
    with open_session() as session:
        names = list(session.scalars(select(CityRow.name)
                     .join(AttractionRow, AttractionRow.city_id == CityRow.id)
                     .distinct().order_by(CityRow.name)))
        repository = KnowledgeRepository(session)
        documents = [d for name in names for d in repository.list_documents(name)]
    index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
    stored = index.collection.get(include=["metadatas"])
    metadata = dict(zip(stored["ids"], stored["metadatas"]))
    missing = [d for d in documents if d.poi_id not in metadata]
    stale = [d for d in documents if d.poi_id in metadata and
             ((metadata[d.poi_id] or {}).get("content_hash") != _content_hash(d)
              or (metadata[d.poi_id] or {}).get("scope") != "all")]
    orphan_ids = sorted(set(stored["ids"]) - {d.poi_id for d in documents})
    stats = {"database_documents": len(documents), "vector_count": len(stored["ids"]),
             "missing": len(missing), "stale": len(stale), "orphans": len(orphan_ids)}
    return settings, index, missing + stale, stats, orphan_ids


async def sync_vectors(commit=False):
    import ssl
    import httpx
    from backend.rag.embedding import OpenAICompatibleEmbeddingClient
    before = schema_snapshot()
    settings, index, needs, stats, orphan_ids = vector_state()
    if commit and needs:
        # Honor Windows certificate trust without disabling TLS verification.
        try:
            import truststore
            tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            tls_context = ssl.create_default_context()
        transport = httpx.AsyncClient(verify=tls_context,
                       timeout=settings.embedding_timeout_seconds)
        client = OpenAICompatibleEmbeddingClient(settings.embedding_api_key,
                    base_url=settings.embedding_base_url, model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                    timeout_seconds=settings.embedding_timeout_seconds, batch_size=10,
                    client=transport)
        try:
            for offset in range(0, len(needs), 10):
                batch = needs[offset:offset+10]
                for attempt in range(3):
                    try:
                        vectors = await client.embed([d.index_text() for d in batch])
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2 ** (attempt+1))
                index.upsert(batch, vectors)
        finally:
            await transport.aclose()
    if commit and orphan_ids:
        backup = index.collection.get(ids=orphan_ids,
                         include=["documents", "metadatas", "embeddings"])
        if backup.get("embeddings") is not None:
            backup["embeddings"] = backup["embeddings"].tolist()
        save(WORK / f"vector_orphans_before_{datetime.now().strftime('%H%M%S')}.json", backup)
        index.collection.delete(ids=orphan_ids)
    _, _, _, after_stats, _ = vector_state()
    if before != schema_snapshot():
        raise RuntimeError("Schema changed during vector synchronization")
    payload = {"committed": commit, "before": stats, "after": after_stats,
               "schema_unchanged": True}
    if commit:
        save(WORK / "vector_sync.json", payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("export","report","import","vectors"))
    parser.add_argument("--commit",action="store_true")
    args = parser.parse_args()
    if args.action == "export":
        result = export()
    elif args.action == "report":
        result = report()
    elif args.action == "vectors":
        result = asyncio.run(sync_vectors(args.commit))
    else:
        result = import_approved(args.commit)
    print(json.dumps(result,ensure_ascii=False,indent=2))

"""Public-coordinate evidence collection only; never approves or imports a POI.

The source-backed maintenance importer is deliberately separate. This script
stores provider responses outside SQL and does not initialize the database.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import ssl
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import nationwide_city_sweep as maintenance

WORK = maintenance.WORK
ROUND = "research_round_20261002B"


def baseline():
    target = WORK / "pending_baseline_B.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    items = []
    for path in sorted(WORK.glob("review_*.json")):
        for review in json.loads(path.read_text(encoding="utf-8")):
            for item in review.get("candidates", []):
                if item.get("status") != "approved":
                    items.append({"file": path.name, "city_id": review["id"],
                                  "city": review["city"], "name": item["name"]})
    result = {"created_at": datetime.now().isoformat(), "count": len(items), "items": items}
    maintenance.save(target, result)
    maintenance.save(WORK / "existing_rows_before_B.json", maintenance.existing_row_fingerprints())
    return result


def candidate_for_target(review, target_name):
    """Resolve a fixed-round target even if its canonical display name changed."""
    matches = [item for item in review.get("candidates", [])
               if item.get("name") == target_name or target_name in item.get("aliases", [])]
    if len(matches) != 1:
        raise ValueError(f"Expected one candidate or alias for {target_name!r}; found {len(matches)}")
    return matches[0]


def lookup(files):
    import httpx
    try:
        import truststore
        tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        tls = ssl.create_default_context()
    baseline_path = WORK / "pending_baseline_B.json"
    if not baseline_path.exists():
        raise RuntimeError("Create baseline before gathering evidence")
    selected = [i for i in json.loads(baseline_path.read_text(encoding="utf-8"))["items"]
                if i["file"] in set(files)]
    output = WORK / "coordinates_B_root.json"
    cache = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    with httpx.Client(verify=tls, timeout=18,
                     headers={"User-Agent": "TravelCatalogResearch/1.0 (public POI verification)"}) as client:
        for index, item in enumerate(selected):
            key = f"{item['city_id']}|{item['name']}"
            if key in cache:
                continue
            # Exact name search first. Fuzzy or wrong-city hits remain raw
            # evidence and MUST NOT be imported automatically.
            params = {"format": "jsonv2", "limit": 5, "countrycodes": "cn",
                      "q": item["name"], "addressdetails": 1}
            started = time.monotonic()
            evidence = {**item, "checked_at": datetime.now().isoformat(),
                        "provider": "OpenStreetMap Nominatim", "query": params,
                        "outcome": "unreviewed"}
            try:
                response = client.get("https://nominatim.openstreetmap.org/search", params=params)
                response.raise_for_status()
                evidence["response_url"] = str(response.url)
                evidence["hits"] = response.json()
            except Exception as exc:
                evidence["error"] = type(exc).__name__
            cache[key] = evidence
            maintenance.save(output, cache)
            print(f"{index+1}/{len(selected)} {item['city']} {item['name']}: "
                  f"{len(evidence.get('hits', []))} raw hits", flush=True)
            time.sleep(max(0, 4.2 - (time.monotonic() - started)))
    return {"selected": len(selected), "cached": len(cache), "path": str(output)}


def progress():
    original = baseline()
    reviews = maintenance.load_reviews()
    seen = {}
    for target in original["items"]:
        review = reviews.get(target["city_id"])
        if review is None:
            continue
        item = candidate_for_target(review, target["name"])
        if item.get(ROUND):
            identity = (target["city_id"], target["name"])
            seen[identity] = item[ROUND].get("outcome", "unresolved")
    counts = {outcome: list(seen.values()).count(outcome) for outcome in sorted(set(seen.values()))}
    with (WORK / "existing_rows_before_B.json").open(encoding="utf-8") as stream:
        prior = json.load(stream)
    current = maintenance.existing_row_fingerprints()
    if any(current.get(key) != value for key, value in prior.items()):
        raise RuntimeError("A pre-existing attraction record changed during round B")
    return {"target": original["count"], "reviewed": len(seen),
            "unreviewed": original["count"]-len(seen), "outcomes": counts,
            "existing_rows_unchanged": len(prior)}


def merge_root():
    """Mechanical merge of reviewed, file-backed evidence; never auto-approves hits."""
    notes = json.loads((WORK / "root_web_evidence_B.json").read_text(encoding="utf-8"))
    cache = json.loads((WORK / "coordinates_B_root.json").read_text(encoding="utf-8"))
    curated = json.loads((WORK / "root_approvals_B.json").read_text(encoding="utf-8"))
    supplemental = {filename: json.loads((WORK / filename).read_text(encoding="utf-8"))
                    for filename in ("photon_B_root.json", "museum_guides_B_root.json")
                    if (WORK / filename).exists()}
    selected_files = {item["file"] for item in notes}
    completed = 0
    for filename in sorted(selected_files):
        path = WORK / filename
        reviews = json.loads(path.read_text(encoding="utf-8"))
        for review in reviews:
            for item in review.get("candidates", []):
                key = f"{review['id']}|{item['name']}"
                note = next((n for n in notes if n["id"] == review["id"] and n["name"] == item["name"]), None)
                if note is None:
                    continue
                coordinate = cache.get(key)
                if coordinate is None:
                    raise RuntimeError(f"Missing this-round coordinate attempt: {key}")
                city_hits = [h for h in coordinate.get("hits", []) if review["city"] in h.get("display_name", "")]
                if coordinate.get("error"):
                    coordinate_note = "公开坐标查询失败：" + coordinate["error"] + "；不使用猜测坐标。"
                elif not coordinate.get("hits"):
                    coordinate_note = "本轮公开地图按名查询未命中；尚未找到可核准的具名入口/场馆坐标。"
                elif not city_hits:
                    coordinate_note = "公开地图返回异地或不相关地点，已拒绝采用。"
                else:
                    coordinate_note = "公开地图命中见coordinates_B_root.json；需核对对象类型、名称及入口，不自动采纳范围中心或同名站点。"
                decision = curated.get(key, {})
                if decision:
                    item.update(decision.get("fields", {}))
                    item["status"] = decision["status"]
                reason = decision.get("reason", "公开资料补查已登记；坐标仍缺可靠入口/场馆核准证据，暂不新增入库。")
                item[ROUND] = {"outcome": decision.get("outcome", "unresolved"), "reason": reason,
                               "source_urls": list(dict.fromkeys([item["description_source_url"]] + note.get("source_urls", []) + decision.get("source_urls", []))),
                               "checked_at": datetime.now().isoformat(),
                               "coordinate_review_note": decision.get("coordinate_review_note", coordinate_note),
                               "web_search": note.get("web_search"),
                               "coordinate_attempt_file": "coordinates_B_root.json", "coordinate_attempt_key": key}
                item[ROUND]["additional_coordinate_attempts"] = [
                    {"file": filename, "key": key, "error": data[key].get("error"),
                     "raw_result_count": len(data[key].get("features", data[key].get("guides", [])))}
                    for filename, data in supplemental.items() if key in data]
                completed += 1
        maintenance.save(path, reviews)
    return {"merged_root_reviews": completed}


def inspect_osm():
    """Inspect explicit reviewed OSM objects, including real tagged entrances."""
    import httpx
    import truststore
    import xml.etree.ElementTree as ET
    wanted = {1125974487, 457587853, 1531003059, 993455721, 1472243299,
              592564699, 1037510498, 746956988, 606536512, 1471785633,
              879506636, 554604783, 1351505184, 12370114529, 6389007114}
    cache = json.loads((WORK / "coordinates_B_root.json").read_text(encoding="utf-8"))
    output = WORK / "osm_objects_B_root.json"
    evidence = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with httpx.Client(verify=tls, timeout=20, headers={"User-Agent": "TravelCatalogResearch/1.0"}) as client:
        for source in cache.values():
            for hit in source.get("hits", []):
                if hit["osm_id"] not in wanted:
                    continue
                key = f"{hit['osm_type']}/{hit['osm_id']}"
                if key in evidence:
                    continue
                url = f"https://api.openstreetmap.org/api/0.6/{key}" + ("/full" if hit["osm_type"] == "way" else "")
                record = {"candidate": source["name"], "city": source["city"], "source_url": url,
                          "checked_at": datetime.now().isoformat()}
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    elements = []
                    for element in ET.fromstring(response.content):
                        if element.tag not in {"node", "way", "relation"}:
                            continue
                        elements.append({"type": element.tag, **element.attrib,
                                         "tags": {t.attrib["k"]: t.attrib["v"] for t in element.findall("tag")},
                                         "nodes": [n.attrib["ref"] for n in element.findall("nd")]})
                    record["elements"] = elements
                except Exception as exc:
                    record["error"] = type(exc).__name__
                evidence[key] = record
                maintenance.save(output, evidence)
                tagged = [e for e in record.get("elements", []) if e.get("tags")]
                print(json.dumps({"object": key, "candidate": source["name"], "error": record.get("error"),
                                  "tagged_elements": tagged}, ensure_ascii=False), flush=True)
    return {"objects": len(evidence), "path": str(output)}


def inspect_entrances():
    """Bounded public OSM inspection; gates remain evidence, not auto-approved coordinates."""
    import httpx
    import truststore
    import xml.etree.ElementTree as ET
    objects = json.loads((WORK / "osm_objects_B_root.json").read_text(encoding="utf-8"))
    wanted = ["way/457587853", "way/1531003059", "way/993455721",
              "way/1472243299", "way/592564699"]
    output = WORK / "entrances_B_root.json"
    evidence = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    with httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=25,
                      headers={"User-Agent": "TravelCatalogResearch/1.0"}) as client:
        for key in wanted:
            if key in evidence:
                continue
            source = objects[key]
            nodes = [e for e in source["elements"] if e["type"] == "node"]
            bbox = [min(float(n["lon"]) for n in nodes)-0.0001,
                    min(float(n["lat"]) for n in nodes)-0.0001,
                    max(float(n["lon"]) for n in nodes)+0.0001,
                    max(float(n["lat"]) for n in nodes)+0.0001]
            record = {"candidate": source["candidate"], "bbox": bbox,
                      "checked_at": datetime.now().isoformat()}
            try:
                response = client.get("https://api.openstreetmap.org/api/0.6/map",
                                      params={"bbox": ",".join(str(v) for v in bbox)})
                response.raise_for_status()
                record["source_url"] = str(response.url)
                elements = []
                for element in ET.fromstring(response.content):
                    if element.tag not in {"node", "way", "relation"}:
                        continue
                    elements.append({"type": element.tag, **element.attrib,
                                     "tags": {t.attrib["k"]: t.attrib["v"] for t in element.findall("tag")},
                                     "nodes": [n.attrib["ref"] for n in element.findall("nd")]})
                record["elements"] = elements
                gates = [e for e in elements if "entrance" in e["tags"] or
                         e["tags"].get("barrier") in {"gate", "lift_gate"}]
                record["gate_candidates"] = gates
            except Exception as exc:
                record["error"] = type(exc).__name__
            evidence[key] = record
            maintenance.save(output, evidence)
            print(json.dumps({"object": key, "candidate": record["candidate"],
                              "error": record.get("error"), "gates": record.get("gate_candidates", [])},
                             ensure_ascii=False), flush=True)
    return {"inspected": len(evidence), "path": str(output)}


def inspect_public_markers():
    """Read published named markers, without loading a map SDK or using any key."""
    import httpx
    import re
    import truststore
    urls = ["https://www.iwenbo.fun/heritage/1196.html",
            "https://www.iwenbo.fun/heritage/3620.html",
            "https://www.iwenbo.fun/museum/1319.html",
            "https://www.iwenbo.fun/museum/4364.html",
            "https://www.iwenbo.fun/museum/5210.html",
            "https://www.iwenbo.fun/museum/4973.html",
            "https://www.iwenbo.fun/museum/1084.html",
            "https://www.iwenbo.fun/museum/5077.html"]
    output = WORK / "published_markers_B_root.json"
    evidence = json.loads(output.read_text(encoding="utf-8")) if output.exists() else []
    cached_urls = {item["source_url"] for item in evidence}
    with httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=30) as client:
        for url in urls:
            if url in cached_urls:
                continue
            response = client.get(url)
            response.raise_for_status()
            match = re.search(r"['\"]position['\"]\s*:\s*new TMap.LatLng\(([\d.]+),\s*([\d.]+)\)", response.text)
            if not match:
                raise RuntimeError(f"Missing published named marker: {url}")
            title = re.search(r"['\"]title['\"]\s*:\s*\"([^\"]+)\"", response.text[match.end():])
            evidence.append({"source_url": url, "checked_at": datetime.now().isoformat(),
                             "latitude": float(match[1]), "longitude": float(match[2]),
                             "marker_name": title[1] if title else None,
                             "marker_code": match[0], "sdk": "TMap", "coordinate_system": "GCJ02",
                             "coordinate_system_reference": "https://github.com/TencentLBS/tencentmap-map-assistant/blob/main/SKILL.md",
                             "note": "公开网页明确具名点标记，非现场实测；未调用地图服务API或加载SDK。"})
            maintenance.save(output, evidence)
    return evidence


def lookup_photon():
    """Additional public-name search; raw responses never imply approval."""
    import httpx
    import truststore
    files = {item["file"] for item in json.loads(
        (WORK / "root_web_evidence_B.json").read_text(encoding="utf-8"))}
    output = WORK / "photon_B_root.json"
    cache = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    selected = [(r, i) for path in sorted(WORK.glob("review_*.json")) if path.name in files
                for r in json.loads(path.read_text(encoding="utf-8"))
                for i in r.get("candidates", [])
                if i.get(ROUND, {}).get("outcome") == "unresolved"]
    with httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=15,
                      headers={"User-Agent": "TravelCatalogResearch/1.0"}) as client:
        for index, (review, item) in enumerate(selected):
            key = f"{review['id']}|{item['name']}"
            if key in cache:
                continue
            record = {"city": review["city"], "name": item["name"],
                      "checked_at": datetime.now().isoformat(), "features": []}
            started = time.monotonic()
            try:
                response = client.get("https://photon.komoot.io/api/",
                                      params={"q": item["name"], "limit": 5, "lang": "default"})
                response.raise_for_status()
                record.update(response_url=str(response.url), features=response.json()["features"])
            except Exception as exc:
                record["error"] = type(exc).__name__
            cache[key] = record
            maintenance.save(output, cache)
            print(f"{index+1}/{len(selected)} {key}: {len(record['features'])} raw hits", flush=True)
            time.sleep(max(0, 1.2 - (time.monotonic()-started)))
    return {"searched": len(selected), "cache": str(output)}


def inspect_museum_guides():
    """Inspect native public directory search; retain raw marker evidence only."""
    import httpx
    import re
    import truststore
    from bs4 import BeautifulSoup
    files = {item["file"] for item in json.loads(
        (WORK / "root_web_evidence_B.json").read_text(encoding="utf-8"))}
    selected = [(r, i) for path in sorted(WORK.glob("review_*.json")) if path.name in files
                for r in json.loads(path.read_text(encoding="utf-8"))
                for i in r.get("candidates", [])
                if i.get(ROUND, {}).get("outcome") == "unresolved" and
                any(word in i["name"] for word in ("馆", "博物苑"))]
    output = WORK / "museum_guides_B_root.json"
    cache = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    with httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=20) as client:
        for review, item in selected:
            key = f"{review['id']}|{item['name']}"
            if key in cache:
                continue
            record = {"name": item["name"], "city": review["city"],
                      "checked_at": datetime.now().isoformat(), "guides": []}
            try:
                response = client.get("https://www.iwenbo.fun/index.php/index/museum/list.html",
                                      params={"keyword": item["name"]})
                response.raise_for_status()
                record["search_url"] = str(response.url)
                soup = BeautifulSoup(response.text, "html.parser")
                urls = sorted({"https://www.iwenbo.fun/museum/" + match[1] + ".html"
                               for a in soup.find_all("a", href=True)
                               if (match := re.search(r"/museum/(\d+)\.html", a["href"]))})
                for url in urls:
                    detail = client.get(url)
                    detail.raise_for_status()
                    match = re.search(r"['\"]position['\"]\s*:\s*new TMap.LatLng\(([\d.]+),\s*([\d.]+)\)", detail.text)
                    parsed = BeautifulSoup(detail.text, "html.parser")
                    title = re.search(r"['\"]title['\"]\s*:\s*\"([^\"]+)\"", detail.text[match.end():]) if match else None
                    record["guides"].append({"url": url, "text": parsed.get_text(" ", strip=True),
                                             "latitude": float(match[1]) if match else None,
                                             "longitude": float(match[2]) if match else None,
                                             "marker_name": title[1] if title else None,
                                             "marker_code": match[0] if match else None,
                                             "coordinate_system": "GCJ02"})
            except Exception as exc:
                record["error"] = type(exc).__name__
            cache[key] = record
            maintenance.save(output, cache)
            print(f"{key}: {len(record['guides'])} guides", flush=True)
            time.sleep(1.2)
    return {"searched": len(selected), "cache": str(output)}


def inspect_heritage_guides():
    """Inspect public heritage directory; no automatic approval or SQL writes."""
    import httpx
    import re
    import truststore
    from bs4 import BeautifulSoup
    queries = ["曹操运兵道", "道德中宫", "曹腾墓", "张园汉墓", "青礁慈济宫",
               "黄楼", "歌风台", "东风堰", "春秋楼"]
    output = WORK / "heritage_guides_B_root.json"
    cache = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    with httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=20) as client:
        for query in queries:
            if query in cache:
                continue
            record = {"query": query, "checked_at": datetime.now().isoformat(), "guides": []}
            try:
                response = client.get("https://www.iwenbo.fun/index.php/index/heritage/list.html",
                                      params={"keyword": query, "level": 0, "batch": 0})
                response.raise_for_status()
                record["search_url"] = str(response.url)
                soup = BeautifulSoup(response.text, "html.parser")
                urls = sorted({"https://www.iwenbo.fun/heritage/" + match[1] + ".html"
                               for a in soup.find_all("a", href=True)
                               if (match := re.search(r"/heritage/(\d+)\.html", a["href"]))})
                for url in urls:
                    detail = client.get(url)
                    detail.raise_for_status()
                    match = re.search(r"['\"]position['\"]\s*:\s*new TMap.LatLng\(([\d.]+),\s*([\d.]+)\)", detail.text)
                    title = re.search(r"['\"]title['\"]\s*:\s*\"([^\"]+)\"", detail.text[match.end():]) if match else None
                    record["guides"].append({"url": url,
                        "text": BeautifulSoup(detail.text, "html.parser").get_text(" ", strip=True),
                        "latitude": float(match[1]) if match else None,
                        "longitude": float(match[2]) if match else None,
                        "marker_name": title[1] if title else None,
                        "marker_code": match[0] if match else None, "coordinate_system": "GCJ02"})
            except Exception as exc:
                record["error"] = type(exc).__name__
            cache[query] = record
            maintenance.save(output, cache)
            print(f"{query}: {len(record['guides'])} raw heritage guides", flush=True)
            time.sleep(1)
    return {"searched": len(cache), "cache": str(output)}


def report():
    summary = progress()
    if summary["unreviewed"]:
        raise RuntimeError("Do not publish a completed report while candidates remain unreviewed")
    targets = baseline()["items"]
    reviews = maintenance.load_reviews()
    prior = json.loads((WORK / "existing_rows_before_B.json").read_text(encoding="utf-8"))
    current = maintenance.existing_row_fingerprints()
    summary["database_count"] = len(current)
    summary["newly_inserted"] = len(set(current) - set(prior))
    summary["schema_unchanged"] = maintenance.schema_snapshot() == json.loads(
        (WORK / "schema_before.json").read_text(encoding="utf-8"))
    if not summary["schema_unchanged"]:
        raise RuntimeError("Database schema differs from the research baseline")
    unresolved = []
    approved = []
    for target in targets:
        review = reviews[target["city_id"]]
        item = candidate_for_target(review, target["name"])
        if item[ROUND]["outcome"] == "unresolved":
            unresolved.append(item)
        elif item[ROUND]["outcome"] == "approved":
            approved.append((review, item))
    summary["unresolved_missing_description"] = sum(not i.get("description") for i in unresolved)
    summary["unresolved_missing_coordinate"] = sum(
        not i.get("coordinate_source_url") or i.get("longitude") is None or i.get("latitude") is None
        for i in unresolved)
    summary["vector_state"] = maintenance.vector_state()[3]
    outcome_labels = {"approved": "证据补齐", "excluded_duplicate": "重复/整体包含，不新增", "unresolved": "仍待补证"}
    lines = ["# 483条缺证候选专项复核", "", f"更新时间：{datetime.now().isoformat(timespec='seconds')}", "",
             f"固定目标：{summary['target']}条；本轮已补查：{summary['reviewed']}条；未补查：{summary['unreviewed']}条。", "",
             "结果统计：" + "；".join(f"{outcome_labels.get(k, k)}：{v}" for k, v in summary["outcomes"].items()) + "。", "",
             f"本轮实际新增入库：{summary['newly_inserted']}条；数据库当前总数：{summary['database_count']}条。", "",
             "approved是本轮补齐证据的候选，不等同于新增入库数；新增数以本轮导入审计为准。未决候选不冒充假景点，也不强行使用猜测坐标；排除项只从外部候选清单排除，不删除现有数据库记录。",
             "本轮未调用高德，不改变数据库表/字段，已有3177条景点逐字段指纹检查未变。详情证据保存在data/city_sweep_20261002/review_*.json的research_round_20261002B中。", "",
             "坐标来源为公开地图具名POI、紧凑馆区/馆舍或核准入口；不冒充现场测量。游览时长是规划估计，不是官方保证值。简介依据来源概述，不虚构门票、开放时间等易变信息。", "",
             f"未决项中，{summary['unresolved_missing_coordinate']}条缺可采纳坐标；其中{summary['unresolved_missing_description']}条还缺可入库的景点简介。查到存在证明或A级名单不等于全部字段齐全。", "",
             f"向量检查：{summary['vector_state']}。", "", "## 本轮补齐的候选", "",
             "|省份|城市/地区|景点|坐标依据|", "|---|---|---|---|"]
    for review, item in approved:
        lines.append(f"|{review['province']}|{review['city']}|{item['name']}|[公开来源]({item['coordinate_source_url']})|")
    lines += ["", "## 483条逐项核查", "",
              "|省份|城市/地区|候选|本轮结果|具体原因|资料|", "|---|---|---|---|---|---|"]
    for target in targets:
        review = reviews[target["city_id"]]
        item = candidate_for_target(review, target["name"])
        evidence = item[ROUND]
        urls = evidence.get("source_urls", [])
        links = "；".join(f"[来源{i+1}]({u})" for i, u in enumerate(urls[:4]))
        reason_parts = [evidence.get("reason", ""), evidence.get("coordinate_review_note", "")]
        reason = "；".join(dict.fromkeys(p for p in reason_parts if p)).replace("|", "/").replace("\n", " ")
        outcome = outcome_labels.get(evidence["outcome"], evidence["outcome"])
        lines.append(f"|{review['province']}|{review['city']}|{item['name']}|{outcome}|{reason}|{links}|")
    path = ROOT / "docs" / "483条缺证候选专项复核-20261002.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    maintenance.save(WORK / "research_round_B_result.json", summary)
    return {**summary, "report": str(path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("baseline", "lookup", "lookup-photon", "progress", "merge-root", "report", "inspect-osm", "inspect-entrances", "inspect-public-markers", "inspect-museum-guides", "inspect-heritage-guides"))
    parser.add_argument("--files", nargs="+", default=[])
    args = parser.parse_args()
    actions = {"baseline": baseline, "lookup": lambda: lookup(args.files), "progress": progress,
               "merge-root": merge_root, "report": report, "inspect-osm": inspect_osm,
               "inspect-entrances": inspect_entrances, "inspect-public-markers": inspect_public_markers,
               "lookup-photon": lookup_photon, "inspect-museum-guides": inspect_museum_guides,
               "inspect-heritage-guides": inspect_heritage_guides}
    result = actions[args.action]()
    if args.action == "baseline":
        result = {"count": result["count"], "created_at": result["created_at"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))

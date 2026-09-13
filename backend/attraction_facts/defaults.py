def category_defaults(category: str | None, name: str) -> tuple[int, str]:
    text = f"{category or ''} {name}"
    if any(keyword in text for keyword in ("博物馆", "美术馆", "纪念馆", "展览馆", "科技馆")):
        return 120, "indoor"
    if any(keyword in text for keyword in ("度假区", "湿地", "森林", "风景区")):
        return 240, "outdoor"
    if any(keyword in text for keyword in ("古镇", "古城")):
        return 180, "outdoor"
    if any(keyword in text for keyword in ("寺", "庙", "塔", "故居", "历史建筑")):
        return 90, "mixed"
    if any(keyword in text for keyword in ("公园", "广场", "自然风光", "山", "湖")):
        return 120, "outdoor"
    if any(keyword in text for keyword in ("剧场", "演出", "影院")):
        return 120, "indoor"
    return 90, "unknown"

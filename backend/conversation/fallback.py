"""Deterministic summaries of already obtained ordinary-mode tool cards."""


def summarize_tool_cards(card: dict | None) -> str:
    if not isinstance(card, dict):
        return ""
    cards = card.get("cards", []) if card.get("type") == "collection" else [card]
    lines: list[str] = []
    for item in cards:
        kind = item.get("type")
        if kind == "weather":
            forecasts = item.get("weather", [])
            for weather in forecasts if isinstance(forecasts, list) else [forecasts]:
                if weather.get("status") != "available":
                    continue
                temperature = ""
                if weather.get("temperature_min") is not None and weather.get("temperature_max") is not None:
                    temperature = f"，{weather['temperature_min']:g}～{weather['temperature_max']:g}℃"
                lines.append(f"{weather['city']} {weather.get('date') or ''}：{weather.get('condition') or '天气状况未提供'}{temperature}。")
        elif kind == "route":
            route = item["route"]
            if route.get("distance_m") is None or route.get("duration_min") is None:
                continue
            mode = {"walking": "步行", "transit": "公共交通", "driving": "驾车"}.get(route.get("mode"), "交通")
            suffix = "（距离与耗时为估算，不是实时导航）" if route.get("estimated") else ""
            lines.append(f"{route['origin_name']} → {route['destination_name']}：{mode}，约 {route['distance_m'] / 1000:g} 公里，{route['duration_min']:g} 分钟{suffix}。")
        elif kind == "nearby":
            nearby = item["nearby"]
            names = "、".join(place["name"] for place in nearby.get("places", [])[:6])
            if names:
                lines.append(f"{nearby['center_name']}附近已获取的{nearby['category']}：{names}。地点详情见卡片，附近距离为直线距离。")
    return "\n".join(lines)

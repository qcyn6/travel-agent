"""Conservative weather-to-travel advice used when the review agent is unavailable."""

from backend.models.weather import WeatherResult


def fallback_travel_advice(weather: WeatherResult) -> str:
    """Create a short, fact-grounded suggestion without changing the itinerary."""

    condition = (weather.condition or "").strip()
    normalized = condition.lower()
    advice: list[str] = []

    if any(token in normalized for token in ("雨", "雪", "rain", "snow", "shower", "storm")):
        advice.append("有降水，建议带伞或雨衣，走路时留意路面湿滑")
    elif any(token in normalized for token in ("晴", "sunny", "clear")):
        advice.append("晴天适合户外游览，记得防晒并及时补水")
    elif any(token in normalized for token in ("多云", "少云", "阴", "cloud", "overcast")):
        advice.append("多云天气适合户外游览，可按早晚温差灵活添衣")
    elif condition:
        advice.append(f"天气为{condition}，建议随身准备适合天气的衣物")
    else:
        advice.append("出行前留意天气变化，并准备适合当天的衣物")

    precipitation = weather.precipitation_probability
    if precipitation is not None and precipitation >= 0.5 and not any(
        token in normalized for token in ("雨", "雪", "rain", "snow", "shower", "storm")
    ):
        advice.append("降水概率较高，建议随身带伞")

    minimum = weather.temperature_min
    maximum = weather.temperature_max
    if maximum is not None and maximum >= 32:
        advice.append("天气较热时注意防晒、补水和适当休息")
    elif minimum is not None and minimum <= 5:
        advice.append("气温偏低，建议增加保暖衣物")
    elif minimum is not None and maximum is not None and maximum - minimum >= 10:
        advice.append("昼夜温差较大，建议分层穿衣")

    return "；".join(advice)[:240]

from datetime import date
from typing import Any

import httpx

from backend.models.weather import WeatherResult


class QWeatherConfigurationError(RuntimeError):
    pass


class QWeatherAPIError(RuntimeError):
    pass


class QWeatherClient:
    def __init__(self, api_key: str | None, *, api_host: str | None, timeout_seconds: float = 8.0, client=None, cache=None, cache_ttl_seconds: int = 3600):
        self.api_key = api_key
        self.api_host = (api_host or "").strip().rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds

    async def get_weather(self, *, city: str, target_date: date, longitude: float, latitude: float) -> WeatherResult:
        if not self.api_key or not self.api_host:
            raise QWeatherConfigurationError("未配置和风天气 API")
        cache_key = f"weather:{city}:{target_date.isoformat()}"
        if self.cache is not None:
            cached = await self.cache.get_json(cache_key)
            if isinstance(cached, dict):
                try:
                    return WeatherResult.model_validate(cached)
                except (TypeError, ValueError):
                    pass
        url = f"https://{self.api_host}/weather/v1/daily/{latitude:.4f}/{longitude:.4f}"
        headers = {"X-QW-Api-Key": self.api_key, "Accept-Encoding": "gzip"}
        params = {"days": "10", "localTime": "true", "lang": "zh"}
        try:
            if self.client is not None:
                response = await self.client.get(url, headers=headers, params=params)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            raise QWeatherAPIError("和风天气网络请求失败") from exc
        if response.status_code != 200:
            raise QWeatherAPIError(f"和风天气返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise QWeatherAPIError("和风天气返回了无效 JSON") from exc
        result = _parse_daily(payload, city=city, target_date=target_date)
        if self.cache is not None:
            await self.cache.set_json(
                cache_key,
                result.model_dump(mode="json"),
                ttl_seconds=self.cache_ttl_seconds,
            )
        return result


def _parse_daily(payload: dict[str, Any], *, city: str, target_date: date) -> WeatherResult:
    days = payload.get("days")
    if not isinstance(days, list):
        raise QWeatherAPIError("和风天气结果缺少每日预报")
    target = target_date.isoformat()
    item = next(
        (value for value in days if isinstance(value, dict) and str(value.get("forecastStartTime", ""))[:10] == target),
        None,
    )
    if item is None:
        raise QWeatherAPIError("目标日期不在天气预报范围内")
    daytime = item.get("daytime") if isinstance(item.get("daytime"), dict) else {}
    condition = daytime.get("condition") if isinstance(daytime.get("condition"), dict) else {}
    precipitation = daytime.get("precipitation") if isinstance(daytime.get("precipitation"), dict) else {}
    wind = daytime.get("wind") if isinstance(daytime.get("wind"), dict) else {}
    return WeatherResult(
        city=city,
        date=target_date,
        condition=condition.get("text"),
        temperature_min=_nested_number(item, "temperatureMin"),
        temperature_max=_nested_number(item, "temperatureMax"),
        precipitation_probability=_number(precipitation.get("probability")),
        wind_level=str(wind.get("scale")) if wind.get("scale") is not None else None,
        status="available",
        source="qweather",
    )


def _nested_number(item: dict[str, Any], key: str) -> float | None:
    value = item.get(key)
    return _number(value.get("value")) if isinstance(value, dict) else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

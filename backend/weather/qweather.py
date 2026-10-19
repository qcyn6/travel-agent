import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any

import httpx

from backend.models.weather import WeatherResult
from backend.observability import observe_external_request


class QWeatherConfigurationError(RuntimeError):
    pass


class QWeatherAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class QWeatherClient:
    def __init__(self, api_key: str | None, *, api_host: str | None, timeout_seconds: float = 8.0, client=None, cache=None, cache_ttl_seconds: int = 3600, max_attempts: int = 2):
        self.api_key = api_key
        self.api_host = (api_host or "").strip().rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_attempts = max(1, min(max_attempts, 2))

    async def get_weather(self, *, city: str, target_date: date, longitude: float, latitude: float) -> WeatherResult:
        results = await self.get_weather_range(
            city=city,
            target_dates=[target_date],
            longitude=longitude,
            latitude=latitude,
        )
        return results[0]

    async def get_weather_range(
        self,
        *,
        city: str,
        target_dates: Sequence[date],
        longitude: float,
        latitude: float,
    ) -> list[WeatherResult]:
        if not self.api_key or not self.api_host:
            raise QWeatherConfigurationError("未配置和风天气 API")
        dates = sorted(set(target_dates))
        if not dates:
            return []

        cached_results: dict[date, WeatherResult] = {}
        missing_dates = []
        if self.cache is not None:
            for target_date in dates:
                cached = await self.cache.get_json(f"weather:{city}:{target_date.isoformat()}")
                if isinstance(cached, dict):
                    try:
                        result = WeatherResult.model_validate(cached)
                        if result.date == target_date:
                            cached_results[target_date] = result
                            continue
                    except (TypeError, ValueError):
                        pass
                missing_dates.append(target_date)
        else:
            missing_dates = dates

        fetched_results: dict[date, WeatherResult] = {}
        if missing_dates:
            payload = await self._fetch_daily_payload(longitude=longitude, latitude=latitude)
            for target_date in missing_dates:
                try:
                    fetched_results[target_date] = _parse_daily(
                        payload,
                        city=city,
                        target_date=target_date,
                    )
                except QWeatherAPIError:
                    raise
            if self.cache is not None:
                for target_date, result in fetched_results.items():
                    await self.cache.set_json(
                        f"weather:{city}:{target_date.isoformat()}",
                        result.model_dump(mode="json"),
                        ttl_seconds=self.cache_ttl_seconds,
                    )
        return [cached_results.get(target_date) or fetched_results[target_date] for target_date in dates]

    async def _fetch_daily_payload(self, *, longitude: float, latitude: float) -> dict[str, Any]:
        url = f"https://{self.api_host}/weather/v1/daily/{latitude:.4f}/{longitude:.4f}"
        headers = {"X-QW-Api-Key": self.api_key, "Accept-Encoding": "gzip"}
        params = {"days": "10", "localTime": "true", "lang": "zh"}
        response = None
        for attempt in range(self.max_attempts):
            try:
                async with observe_external_request("qweather", "forecast"):
                    if self.client is not None:
                        response = await self.client.get(url, headers=headers, params=params)
                    else:
                        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                            response = await client.get(url, headers=headers, params=params)
            except httpx.RequestError as exc:
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(0.25)
                    continue
                raise QWeatherAPIError("和风天气网络请求失败") from exc

            retryable_status = response.status_code in {408, 429} or response.status_code >= 500
            if response.status_code == 200:
                break
            if attempt + 1 < self.max_attempts and retryable_status:
                await asyncio.sleep(0.25)
                continue
            raise QWeatherAPIError(f"和风天气返回 HTTP {response.status_code}", retryable=retryable_status)

        if response is None or response.status_code != 200:
            raise QWeatherAPIError("和风天气网络请求失败")
        try:
            payload = response.json()
        except ValueError as exc:
            raise QWeatherAPIError("和风天气返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise QWeatherAPIError("和风天气结果格式无效")
        return payload


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

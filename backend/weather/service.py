import asyncio
import logging
from datetime import date, timedelta

from backend.models.travel_plan import TravelDay
from backend.models.weather import WeatherResult
from backend.weather.qweather import QWeatherAPIError, QWeatherConfigurationError

logger = logging.getLogger(__name__)


class WeatherEnrichmentService:
    def __init__(self, client, *, max_concurrency: int = 3, today_provider=date.today):
        self.client = client
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self.today_provider = today_provider

    async def enrich_day(self, day: TravelDay) -> TravelDay:
        if day.date is None:
            return day.model_copy(update={"weather": self._empty(day, "skipped")})
        today = self.today_provider()
        if day.date < today or day.date > today + timedelta(days=9):
            return day.model_copy(update={"weather": self._empty(day, "unavailable")})
        first = day.attractions[0]
        if (
            (first.longitude is None or first.latitude is None)
            and getattr(self.client, "requires_coordinates", True)
        ):
            return day.model_copy(update={"weather": self._empty(day, "unavailable")})
        if self.client is None:
            return day.model_copy(update={"weather": self._empty(day, "unavailable")})
        try:
            async with self._semaphore:
                weather = await self.client.get_weather(
                    city=day.city,
                    target_date=day.date,
                    longitude=first.longitude,
                    latitude=first.latitude,
                )
        except (QWeatherAPIError, QWeatherConfigurationError) as exc:
            logger.warning(
                "和风天气服务不可用，跳过天气硬判断：%s (%s)",
                day.city,
                type(exc).__name__,
            )
            weather = self._empty(day, "unavailable")
        except Exception as exc:
            logger.warning(
                "天气服务出现未预期错误，跳过天气硬判断：%s (%s)",
                day.city,
                type(exc).__name__,
            )
            weather = self._empty(day, "unavailable")
        return day.model_copy(update={"weather": weather})

    @staticmethod
    def _empty(day: TravelDay, status: str) -> WeatherResult:
        return WeatherResult(city=day.city, date=day.date, status=status, source="none")

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str | None = None
    llm_provider: str = "deepseek"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 30.0
    amap_api_key: str | None = None
    amap_base_url: str = "https://restapi.amap.com"
    amap_timeout_seconds: float = 8.0
    amap_max_retries: int = 3
    route_timeout_seconds: float = 8.0
    route_max_retries: int = 2
    qweather_api_key: str | None = None
    qweather_api_host: str | None = None
    qweather_timeout_seconds: float = 8.0
    schedule_day_start_time: str = "09:00"
    schedule_meal_minutes: int = 90
    schedule_buffer_minutes: int = 60
    evaluator_max_day_minutes: int = 600
    evaluator_max_route_segment_minutes: int = 60
    evaluator_max_daily_route_minutes: int = 180
    evaluator_rain_probability_threshold: float = 0.5
    evaluator_outdoor_ratio_threshold: float = 0.5
    evaluator_minimum_interest_coverage: float = 0.5
    max_replan_count: int = 2
    redis_url: str | None = None
    redis_key_prefix: str = "travel-agent"
    redis_timeout_seconds: float = 1.0
    redis_poi_ttl_seconds: int = 86400
    redis_route_ttl_seconds: int = 10800
    redis_weather_ttl_seconds: int = 3600
    redis_search_ttl_seconds: int = 43200
    redis_wsl_distro: str | None = None
    redis_wsl_startup_timeout_seconds: float = 5.0
    web_search_api_key: str | None = None
    web_search_base_url: str = "https://api.tavily.com"
    web_search_timeout_seconds: float = 15.0
    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"),
    )
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = 30.0
    chroma_path: str = "data/chroma"
    chroma_collection: str = "attraction_knowledge"


@lru_cache
def get_settings() -> Settings:
    return Settings()

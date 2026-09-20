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
    amap_timeout_seconds: float = 8.0
    amap_max_retries: int = 3
    amap_mcp_url: str = "https://mcp.amap.com/mcp"
    amap_mcp_timeout_seconds: float = 15.0
    amap_mcp_sse_read_timeout_seconds: float = 20.0
    amap_mcp_detail_concurrency: int = 5
    route_enrichment_concurrency: int = 3
    qweather_api_key: str | None = None
    qweather_api_host: str | None = None
    qweather_timeout_seconds: float = 8.0
    weather_enrichment_concurrency: int = 3
    schedule_day_start_time: str = "08:00"
    schedule_meal_minutes: int = 40
    schedule_buffer_minutes: int = 20
    evaluator_max_day_minutes: int = 600
    evaluator_max_route_segment_minutes: int = 60
    evaluator_max_daily_route_minutes: int = 180
    evaluator_minimum_interest_coverage: float = 0.5
    redis_url: str | None = None
    redis_key_prefix: str = "travel-agent"
    redis_timeout_seconds: float = 1.0
    redis_poi_ttl_seconds: int = 86400
    redis_route_ttl_seconds: int = 10800
    redis_weather_ttl_seconds: int = 3600
    redis_wsl_distro: str | None = None
    redis_wsl_startup_timeout_seconds: float = 5.0
    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"),
    )
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = 30.0
    rerank_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RERANK_API_KEY", "DASHSCOPE_API_KEY"),
    )
    rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    rerank_model: str = "qwen3-rerank"
    rerank_instruct: str = "Retrieve semantically similar text."
    rerank_timeout_seconds: float = 30.0
    rerank_max_retries: int = 2
    chroma_path: str = "data/chroma"
    chroma_collection: str = "attraction_knowledge"
    knowledge_path: str = "data/knowledge"
    knowledge_chroma_collection: str = "travel_knowledge"
    knowledge_retrieval_top_k: int = 4
    knowledge_embedding_batch_size: int = 5
    planning_heartbeat_seconds: float = 10.0
    planning_worker_stale_seconds: float = 90.0
    planning_queue_enabled: bool = False
    planning_queue_block_seconds: float = 5.0
    planning_queue_dedup_ttl_seconds: int = 86400
    planning_queue_max_length: int = 10000
    alert_webhook_url: str | None = None
    alert_timeout_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

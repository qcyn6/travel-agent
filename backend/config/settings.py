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

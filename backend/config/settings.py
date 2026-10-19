from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    auth_required: bool = False
    auth_secret: str = "change-me-in-production"
    access_token_expire_minutes: int = 720
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    database_url: str | None = None
    llm_provider: str = "deepseek"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_timeout_seconds: float = 30.0
    planning_dialogue_turn_timeout_seconds: float = Field(default=90.0, gt=0, le=300)
    general_turn_timeout_seconds: float = Field(default=90.0, gt=0, le=300)
    general_memory_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    general_route_timeout_seconds: float = Field(default=25.0, gt=0, le=120)
    general_weather_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    general_nearby_timeout_seconds: float = Field(default=25.0, gt=0, le=120)
    general_guide_timeout_seconds: float = Field(default=8.0, gt=0, le=120)
    general_max_output_tokens: int = Field(default=1000, ge=128, le=4000)
    planning_agent_backend: str = "langchain"
    planning_checkpoint_path: str | None = "data/planning_checkpoints.sqlite3"
    amap_api_key: str | None = None
    amap_timeout_seconds: float = 8.0
    amap_max_retries: int = 3
    hotel_enrichment_concurrency: int = 2
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
    evaluator_max_route_segment_minutes: int = 60
    evaluator_max_daily_route_minutes: int = 180
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
    chroma_path: str = "data/chroma"
    chroma_collection: str = "attraction_knowledge"
    guide_knowledge_path: str = "data/knowledge/guides"
    guide_knowledge_chroma_collection: str = "travel_guides"
    knowledge_embedding_batch_size: int = 5
    planning_heartbeat_seconds: float = 10.0
    planning_worker_stale_seconds: float = 90.0
    planning_queue_enabled: bool = False
    planning_queue_block_seconds: float = 5.0
    planning_queue_dedup_ttl_seconds: int = 86400
    planning_queue_max_length: int = 10000
    alert_webhook_url: str | None = None
    alert_timeout_seconds: float = 5.0
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    rate_limit_enabled: bool = True
    login_requests_per_minute: int = Field(default=20, ge=1)
    register_requests_per_hour: int = Field(default=10, ge=1)
    user_requests_per_minute: int = Field(default=600, ge=1)
    user_compute_requests_per_minute: int = Field(default=20, ge=1)
    user_compute_requests_per_day: int = Field(default=100, ge=1)
    global_compute_requests_per_minute: int = Field(default=40, ge=1)
    global_compute_requests_per_day: int = Field(default=500, ge=1)
    planning_max_active_jobs: int = Field(default=4, ge=1, le=100)
    planning_max_active_jobs_per_user: int = Field(default=1, ge=1, le=10)
    planning_jobs_per_user_per_day: int = Field(default=10, ge=1)
    planning_jobs_per_day: int = Field(default=50, ge=1)
    database_auto_initialize: bool = True

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        return value.strip().lower()

    def validate_production_security(self) -> None:
        """Reject insecure deployment settings without printing secret values."""
        if self.app_env != "production":
            return
        placeholders = {
            "change-me-in-production", "please-change-this-in-production",
            "请替换为随机长字符串",
        }
        if self.auth_secret.strip() in placeholders or len(self.auth_secret.strip().encode("utf-8")) < 32:
            raise ValueError("生产环境必须设置独立随机的 AUTH_SECRET（至少 32 字节），不能使用示例密钥")
        if len(set(self.auth_secret.strip())) < 8:
            raise ValueError("生产环境 AUTH_SECRET 必须使用安全随机生成的密钥")
        if not self.redis_url or not self.rate_limit_enabled:
            raise ValueError("生产环境必须配置 Redis 并启用请求限流")
        if not self.planning_queue_enabled:
            raise ValueError("生产环境必须启用独立规划任务队列")
        if self.database_auto_initialize:
            raise ValueError("生产环境必须关闭自动初始化；请在维护步骤显式迁移数据库")
        from urllib.parse import urlsplit
        if not self.cors_origins or any(
            urlsplit(origin).scheme != "https" or not urlsplit(origin).hostname
            or urlsplit(origin).username or urlsplit(origin).path not in ("", "/")
            or urlsplit(origin).query or urlsplit(origin).fragment
            for origin in self.cors_origins
        ):
            raise ValueError("生产环境 CORS_ORIGINS 必须明确配置 HTTPS 前端域名")
        if not self.database_url:
            raise ValueError("生产环境必须配置独立数据库")
        from sqlalchemy.engine import make_url
        database = make_url(self.database_url)
        if database.username in {"root", "admin"}:
            raise ValueError("生产环境不能使用数据库超级用户，请配置专用业务账号")
        if self.bootstrap_admin_password and (
            len(self.bootstrap_admin_password) < 12
            or self.bootstrap_admin_password in {"change-this-password", "please-change-this-password"}
        ):
            raise ValueError("生产环境初始管理员密码必须至少12位且不能使用示例密码")


@lru_cache
def get_settings() -> Settings:
    return Settings()

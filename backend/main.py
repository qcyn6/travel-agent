from contextlib import asynccontextmanager
import asyncio
import time

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.api.attractions import router as attractions_router
from backend.api.admin import router as admin_router
from backend.api.auth import router as auth_router
from backend.api.conversations import router as conversations_router
from backend.api.general_conversations import router as general_conversations_router
from backend.api.knowledge import router as knowledge_router
from backend.api.planning import router as planning_router
from backend.api.requirements import router as requirements_router
from backend.api.retrieval import router as retrieval_router
from backend.api.travel import router as travel_router
from backend.database import init_database, open_session
from backend.database.seed import seed_catalog
from backend.auth import UserRepository, get_current_user, require_admin
from backend.config import get_settings
from backend.observability import metrics
from backend.security import PublicProtectionMiddleware, RequestLimiter
from backend.observability.redaction import install_redaction


def create_app(*, initialize_database: bool = True) -> FastAPI:
    configuration = get_settings()
    limiter = RequestLimiter()
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_database:
            settings = get_settings()
            settings.validate_production_security()
            install_redaction(settings)
            if settings.database_auto_initialize:
                init_database()
                with open_session() as session:
                    seed_catalog(session)
                    if settings.bootstrap_admin_username and settings.bootstrap_admin_password:
                        UserRepository(session).ensure_bootstrap_admin(
                            username=settings.bootstrap_admin_username,
                            password=settings.bootstrap_admin_password,
                        )
        try:
            yield
        finally:
            limiter.close()

    production = configuration.app_env == "production"
    created_app = FastAPI(
        title="个性化旅游规划系统 API", version="0.1.0", lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    # Test applications are created without a database and retain the legacy
    # anonymous behavior so the API test suite can use repository doubles.
    created_app.state.auth_bypass = not initialize_database and not production
    created_app.state.request_limiter = limiter
    created_app.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Conversation-Id", "X-Planning-Job-Id", "X-Request-Id"],
    )
    created_app.include_router(conversations_router)
    created_app.include_router(general_conversations_router)
    created_app.include_router(auth_router)
    created_app.include_router(admin_router)
    created_app.include_router(requirements_router, dependencies=[Depends(get_current_user)])
    created_app.add_middleware(PublicProtectionMiddleware, settings_provider=get_settings)
    @created_app.middleware("http")
    async def observe_http_requests(request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_name = getattr(route, "path", "unmatched")
            labels = {
                "method": request.method,
                "route": route_name,
                "status": str(status_code),
            }
            metrics.inc("travel_agent_http_requests_total", labels)
            metrics.observe(
                "travel_agent_http_request_duration_seconds",
                time.perf_counter() - started,
                labels,
            )

    created_app.include_router(attractions_router, dependencies=[Depends(get_current_user)])
    created_app.include_router(knowledge_router, dependencies=[Depends(get_current_user)])
    created_app.include_router(retrieval_router, dependencies=[Depends(get_current_user)])
    created_app.include_router(planning_router, dependencies=[Depends(get_current_user)])
    created_app.include_router(travel_router)

    @created_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "milestone": "Final M12"}

    @created_app.get("/ready", include_in_schema=False)
    async def readiness():
        def check_dependencies():
            from sqlalchemy import text
            from redis import Redis
            with open_session() as session:
                session.execute(text("SELECT 1"))
            settings = get_settings()
            if settings.planning_queue_enabled or settings.app_env == "production":
                if not settings.redis_url:
                    raise RuntimeError("Redis not configured")
                with Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                    client.ping()
        try:
            await asyncio.wait_for(asyncio.to_thread(check_dependencies), timeout=3)
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    @created_app.get("/metrics", include_in_schema=False,
                     dependencies=[Depends(require_admin)] if production else [])
    def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4",
        )

    return created_app


app = create_app()

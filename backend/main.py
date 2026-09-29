from contextlib import asynccontextmanager
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

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
from backend.auth import UserRepository
from backend.config import get_settings
from backend.observability import metrics


def create_app(*, initialize_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_database:
            init_database()
            with open_session() as session:
                seed_catalog(session)
                settings = get_settings()
                if settings.bootstrap_admin_username and settings.bootstrap_admin_password:
                    UserRepository(session).ensure_bootstrap_admin(
                        username=settings.bootstrap_admin_username,
                        password=settings.bootstrap_admin_password,
                    )
        yield

    created_app = FastAPI(title="个性化旅游规划系统 API", version="0.1.0", lifespan=lifespan)
    # Test applications are created without a database and retain the legacy
    # anonymous behavior so the API test suite can use repository doubles.
    created_app.state.auth_bypass = not initialize_database
    created_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Conversation-Id", "X-Planning-Job-Id"],
    )
    created_app.include_router(conversations_router)
    created_app.include_router(general_conversations_router)
    created_app.include_router(auth_router)
    created_app.include_router(admin_router)
    created_app.include_router(requirements_router)
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

    created_app.include_router(attractions_router)
    created_app.include_router(knowledge_router)
    created_app.include_router(retrieval_router)
    created_app.include_router(planning_router)
    created_app.include_router(travel_router)

    @created_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "milestone": "Final M12"}

    @created_app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4",
        )

    return created_app


app = create_app()

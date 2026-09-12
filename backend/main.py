from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.attractions import router as attractions_router
from backend.api.knowledge import router as knowledge_router
from backend.api.planning import router as planning_router
from backend.api.requirements import router as requirements_router
from backend.api.retrieval import router as retrieval_router
from backend.api.travel import router as travel_router
from backend.database import init_database, open_session
from backend.database.seed import seed_catalog


def create_app(*, initialize_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_database:
            init_database()
            with open_session() as session:
                seed_catalog(session)
        yield

    created_app = FastAPI(title="个性化旅游规划系统 API", version="0.1.0", lifespan=lifespan)
    created_app.include_router(requirements_router)
    created_app.include_router(attractions_router)
    created_app.include_router(knowledge_router)
    created_app.include_router(retrieval_router)
    created_app.include_router(planning_router)
    created_app.include_router(travel_router)

    @created_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "milestone": "M6"}

    return created_app


app = create_app()

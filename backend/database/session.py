from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


class DatabaseConfigurationError(RuntimeError):
    pass


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        database_url = get_settings().database_url
        if not database_url:
            raise DatabaseConfigurationError("未配置 MySQL DATABASE_URL")
        if not database_url.startswith("mysql+"):
            raise DatabaseConfigurationError("DATABASE_URL 必须使用 MySQL SQLAlchemy 驱动")
        _engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
    return _engine


def init_database() -> None:
    # Importing tables registers all metadata before schema creation.
    from backend.database import tables as _tables  # noqa: F401
    from backend.database.migrations import upgrade_schema

    engine = get_engine()
    Base.metadata.create_all(engine)
    upgrade_schema(engine)


def open_session() -> Session:
    return Session(get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with open_session() as session:
        yield session

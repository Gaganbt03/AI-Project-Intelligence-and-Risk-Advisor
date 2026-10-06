from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _db_url() -> str:
    url = get_settings().DATABASE_URL
    return url


def _make_engine():
    url = _db_url()
    kwargs: dict = {"connect_args": {}}
    if url.startswith("sqlite"):
        kwargs["connect_args"]["check_same_thread"] = False

        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    else:
        engine = create_engine(url, **kwargs)
    return engine


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (ensure models are registered)
    from app import models_m3  # noqa: F401  (milestone 3 models)

    # `create_all` issues CREATE TABLE IF NOT EXISTS, so this is additive:
    # existing Milestone 1/2 tables and every row in them are left untouched.
    Base.metadata.create_all(bind=engine)
"""SQLAlchemy engine/session setup."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_db_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    # The background task and request handlers run in different threads.
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL lets clients poll job status while the background task writes.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables if they do not exist yet."""
    from app.db import models  # noqa: F401  (registers the models on Base)

    Base.metadata.create_all(engine)

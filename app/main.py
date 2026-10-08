"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import certificates, jobs
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db.database import create_db_engine, create_session_factory, init_db

DESCRIPTION = """
Submit **one request for many recipients**; certificates are generated in the background.

1. `POST /api/v1/jobs` - returns a `job_id` immediately (202).
2. `GET /api/v1/jobs/{job_id}` - poll status and progress.
3. `GET /api/v1/jobs/{job_id}/certificates` - per-recipient results.
4. Download one PDF or a ZIP of all generated PDFs.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name, description=DESCRIPTION, version="1.0.0", lifespan=lifespan
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)

    register_exception_handlers(app)
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(certificates.router, prefix="/api/v1")
    return app


app = create_app()

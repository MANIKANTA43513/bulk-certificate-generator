"""Shared fixtures. Every test gets its own temporary database and certificate directory."""

import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.main import create_app

PayloadFactory = Callable[..., dict[str, Any]]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "certificates",
        organization_name="Test Academy",
        max_recipients_per_job=50,
        _env_file=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # TestClient runs background tasks before returning the response, so a
    # POST /jobs call has finished processing by the time the test sees it.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def session_factory(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def make_payload() -> PayloadFactory:
    def factory(count: int = 3, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_name": "Python Backend Workshop",
            "course_name": "Advanced Python",
            "certificate_date": "2026-10-07",
            "recipients": [
                {"name": f"Person {i}", "email": f"person{i}@example.com"} for i in range(count)
            ],
        }
        payload.update(overrides)
        return payload

    return factory


@pytest.fixture
def submit(client: TestClient, make_payload: PayloadFactory) -> Callable[..., dict[str, Any]]:
    """POST a job (expecting 202) and return the response body."""

    def _submit(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = client.post("/api/v1/jobs", json=payload or make_payload())
        assert response.status_code == 202, response.text
        return response.json()

    return _submit


def pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    assert len(reader.pages) == 1
    return reader.pages[0].extract_text()

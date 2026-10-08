"""Creating jobs, bulk submission and top-level API behaviour."""

from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from app.db.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas.job import JobCreateRequest
from app.services import job_service


def test_create_job_returns_202_with_job_id(client, make_payload):
    response = client.post("/api/v1/jobs", json=make_payload(3))

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["total_recipients"] == 3
    assert body["status"] == "PENDING"  # status at creation time, before background processing
    assert body["rejected_recipients"] == []


def test_job_and_certificates_are_persisted(client, db, make_payload, submit):
    job_id = submit(make_payload(4))["job_id"]

    job = db.get(GenerationJob, job_id)
    assert job.event_name == "Python Backend Workshop"
    assert job.course_name == "Advanced Python"
    assert job.certificate_date.isoformat() == "2026-10-07"
    assert job.total_recipients == 4
    assert len(job.certificates) == 4
    assert all(cert.job_id == job_id for cert in job.certificates)


def test_bulk_job_with_many_recipients_in_one_request(client, make_payload, submit):
    job_id = submit(make_payload(30))["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["total_recipients"] == 30
    assert status["successful_count"] == 30
    assert status["status"] == "COMPLETED"


def test_new_job_is_pending_until_processed(db, session_factory, settings, make_payload):
    request = JobCreateRequest(**make_payload(2))
    job, _ = job_service.create_job(db, request, settings.max_recipients_per_job)

    assert job.status == JobStatus.PENDING
    assert job.started_at is None and job.completed_at is None
    assert all(cert.status == CertificateStatus.PENDING for cert in job.certificates)

    job_service.process_job(job.id, session_factory, settings)

    with session_factory() as fresh:
        assert fresh.get(GenerationJob, job.id).status == JobStatus.COMPLETED


def test_whitespace_is_trimmed_and_collapsed(client, make_payload, submit):
    payload = make_payload(
        event_name="  Python Workshop  ",
        recipients=[{"name": "  John    Doe  ", "email": "  john@example.com "}],
    )
    job_id = submit(payload)["job_id"]

    certificate = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"][0]
    assert certificate["recipient_name"] == "John Doe"
    assert certificate["recipient_email"] == "john@example.com"
    assert client.get(f"/api/v1/jobs/{job_id}").json()["event_name"] == "Python Workshop"


def test_certificate_numbers_are_unique_across_jobs(client, db, make_payload, submit):
    submit(make_payload(10))
    submit(make_payload(10))

    numbers = [cert.certificate_number for cert in db.query(Certificate).all()]
    assert len(numbers) == 20
    assert len(set(numbers)) == 20


def test_unexpected_error_returns_clean_500_without_stack_trace(settings, make_payload):
    from app.main import create_app

    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        with patch("app.services.job_service.get_job", side_effect=RuntimeError("secret internals")):
            response = test_client.get("/api/v1/jobs/anything")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret internals" not in response.text
    assert "Traceback" not in response.text


def test_swagger_and_redoc_are_available(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/jobs" in paths
    assert "/api/v1/jobs/{job_id}" in paths
    assert "/api/v1/jobs/{job_id}/certificates" in paths
    assert "/api/v1/jobs/{job_id}/certificates/download" in paths
    assert "/api/v1/certificates/{certificate_id}" in paths


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/jobs"),
        ("put", "/api/v1/jobs"),
        ("post", "/api/v1/jobs/some-id"),
        ("delete", "/api/v1/jobs/some-id"),
        ("post", "/api/v1/jobs/some-id/certificates"),
        ("post", "/api/v1/jobs/some-id/certificates/download"),
        ("post", "/api/v1/certificates/some-id"),
        ("delete", "/api/v1/certificates/some-id"),
    ],
)
def test_wrong_http_methods_return_405(client, method, url):
    assert getattr(client, method)(url).status_code == 405


def test_malformed_json_body_returns_422(client):
    response = client.post("/api/v1/jobs", content="{not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 422


def test_env_example_is_a_valid_configuration():
    from pathlib import Path

    from app.core.config import Settings

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    loaded = Settings(_env_file=env_example)

    assert loaded.max_recipients_per_job == 1000
    assert loaded.organization_name == "Example Learning Academy"
    assert loaded.database_url.startswith("sqlite:///")

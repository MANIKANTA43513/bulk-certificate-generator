"""Job status, lifecycle and progress calculation."""

from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import GenerationJob, JobStatus
from app.schemas.job import JobCreateRequest, JobStatusResponse
from app.services import job_service
from app.utils import pdf_generator

REAL_RENDER = pdf_generator.render_certificate_pdf


def test_completed_job_status_fields(client, submit, make_payload):
    job_id = submit(make_payload(5))["job_id"]

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["job_id"] == job_id
    assert body["status"] == "COMPLETED"
    assert body["total_recipients"] == 5
    assert body["successful_count"] == 5
    assert body["failed_count"] == 0
    assert body["processed_count"] == 5
    assert body["progress_percentage"] == 100.0
    assert body["created_at"] and body["started_at"] and body["completed_at"]
    assert body["created_at"] <= body["started_at"] <= body["completed_at"]


def test_pending_job_has_zero_progress_and_no_timestamps(db, settings, make_payload):
    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(4)), 50)

    body = JobStatusResponse.from_job(job)
    assert body.status == JobStatus.PENDING
    assert body.progress_percentage == 0.0
    assert body.processed_count == 0
    assert body.started_at is None and body.completed_at is None


def test_progress_is_visible_while_job_is_processing(
    client, db, session_factory, settings, make_payload
):
    """Take a snapshot of the job each time a certificate starts rendering."""
    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(5)), 50)
    snapshots: list[tuple[str, int, int, float]] = []

    def spying_render(data, output_path):
        with session_factory() as other_session:
            current = JobStatusResponse.from_job(other_session.get(GenerationJob, job.id))
        snapshots.append(
            (current.status.value, current.successful_count, current.failed_count, current.progress_percentage)
        )
        REAL_RENDER(data, output_path)

    with patch.object(pdf_generator, "render_certificate_pdf", spying_render):
        job_service.process_job(job.id, session_factory, settings)

    assert snapshots == [
        ("PROCESSING", 0, 0, 0.0),
        ("PROCESSING", 1, 0, 20.0),
        ("PROCESSING", 2, 0, 40.0),
        ("PROCESSING", 3, 0, 60.0),
        ("PROCESSING", 4, 0, 80.0),
    ]
    assert client.get(f"/api/v1/jobs/{job.id}").json()["status"] == "COMPLETED"


def test_rejected_recipients_count_toward_progress(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "A", "email": "a@example.com"},
            {"name": "B", "email": "b@example.com"},
            {"name": "C", "email": "c@example.com"},
            {"name": "D", "email": "bad"},
        ]
    )
    job_id = submit(payload)["job_id"]

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "COMPLETED_WITH_ERRORS"
    assert (body["successful_count"], body["failed_count"], body["processed_count"]) == (3, 1, 4)
    assert body["progress_percentage"] == 100.0


def test_progress_percentage_is_rounded_to_two_decimals():
    job = GenerationJob(
        id="x", event_name="E", course_name="C", certificate_date=datetime.now(UTC).date(),
        status=JobStatus.PROCESSING, total_recipients=3, successful_count=1, failed_count=0,
        created_at=datetime.now(UTC), started_at=None, completed_at=None,
    )

    assert JobStatusResponse.from_job(job).progress_percentage == 33.33


def test_unknown_job_returns_404(client):
    response = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"detail": "Generation job not found"}


def test_progress_never_reaches_100_percent_before_job_is_finished(
    db, session_factory, settings, make_payload
):
    """Every commit the worker makes must leave the job in a consistent state."""
    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(3)), 50)
    committed_states: list[tuple[JobStatus, int, int]] = []

    def record_commit(session: Session) -> None:
        for obj in session.identity_map.values():
            if isinstance(obj, GenerationJob):
                processed = obj.successful_count + obj.failed_count
                committed_states.append((obj.status, processed, obj.total_recipients))

    event.listen(Session, "after_commit", record_commit)
    try:
        job_service.process_job(job.id, session_factory, settings)
    finally:
        event.remove(Session, "after_commit", record_commit)

    assert committed_states[-1] == (JobStatus.COMPLETED, 3, 3)
    for status, processed, total in committed_states[:-1]:
        assert status == JobStatus.PROCESSING
        assert processed < total


def test_polling_a_running_job_never_shows_an_impossible_state(client, db, session_factory, settings, make_payload):
    """Poll the real API from the main thread while another thread processes a job."""
    import threading

    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(120)), 200)
    worker = threading.Thread(target=job_service.process_job, args=(job.id, session_factory, settings))

    samples = []
    worker.start()
    while worker.is_alive():
        samples.append(client.get(f"/api/v1/jobs/{job.id}").json())
    worker.join()
    samples.append(client.get(f"/api/v1/jobs/{job.id}").json())

    seen_processed = 0
    for body in samples:
        processed = body["successful_count"] + body["failed_count"]
        assert body["processed_count"] == processed
        assert processed >= seen_processed  # progress never goes backwards
        seen_processed = processed
        assert body["progress_percentage"] == round(processed / body["total_recipients"] * 100, 2)
        if body["status"] in ("PENDING", "PROCESSING"):
            assert processed < body["total_recipients"]
            assert body["completed_at"] is None
        else:
            assert processed == body["total_recipients"]
            assert body["completed_at"] is not None
    assert samples[-1]["status"] == "COMPLETED"

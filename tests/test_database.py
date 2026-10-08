"""Schema integrity: foreign keys, uniqueness, NOT NULL and cascading deletes."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Certificate, CertificateStatus, GenerationJob, JobStatus


def new_job(**overrides) -> GenerationJob:
    values = dict(
        event_name="E", course_name="C", certificate_date=date(2026, 10, 7),
        status=JobStatus.PENDING, total_recipients=1,
    )
    values.update(overrides)
    return GenerationJob(**values)


def new_certificate(job_id: str, position: int = 0, number: str = "CERT-A") -> Certificate:
    return Certificate(
        job_id=job_id, position=position, recipient_name="A", recipient_email="a@example.com",
        certificate_number=number, status=CertificateStatus.PENDING,
    )


def test_defaults_are_applied_on_insert(db):
    job = new_job()
    db.add(job)
    db.commit()

    assert len(job.id) == 36  # UUID string
    assert (job.successful_count, job.failed_count) == (0, 0)
    assert job.created_at is not None and job.created_at.tzinfo is not None
    assert job.started_at is None and job.completed_at is None


def test_certificate_cannot_reference_a_missing_job(db):
    db.add(new_certificate("no-such-job"))

    with pytest.raises(IntegrityError):
        db.commit()


def test_certificate_numbers_must_be_unique(db):
    job = new_job()
    job.certificates = [new_certificate("", 0, "CERT-SAME"), new_certificate("", 1, "CERT-SAME")]
    db.add(job)

    with pytest.raises(IntegrityError):
        db.commit()


def test_position_must_be_unique_within_a_job(db):
    job = new_job()
    job.certificates = [new_certificate("", 0, "CERT-1"), new_certificate("", 0, "CERT-2")]
    db.add(job)

    with pytest.raises(IntegrityError):
        db.commit()


def test_required_columns_reject_null(db):
    db.add(GenerationJob(event_name=None, course_name="C", certificate_date=date(2026, 10, 7), total_recipients=1))

    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_a_job_removes_its_certificates_and_leaves_other_jobs_alone(db, submit):
    first = submit()["job_id"]
    second = submit()["job_id"]

    db.delete(db.get(GenerationJob, first))
    db.commit()

    assert db.scalars(select(Certificate).where(Certificate.job_id == first)).all() == []
    assert len(db.scalars(select(Certificate).where(Certificate.job_id == second)).all()) == 3


def test_database_level_cascade_works_without_the_orm(db, submit):
    from sqlalchemy import text

    job_id = submit()["job_id"]

    db.execute(text("DELETE FROM generation_jobs WHERE id = :id"), {"id": job_id})
    db.commit()

    assert db.scalars(select(Certificate).where(Certificate.job_id == job_id)).all() == []


def test_a_job_orders_its_certificates_by_request_position(db, submit):
    job_id = submit()["job_id"]

    job = db.get(GenerationJob, job_id)
    assert [c.position for c in job.certificates] == [0, 1, 2]

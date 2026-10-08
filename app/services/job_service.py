"""Creating generation jobs and processing them in the background."""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.models import Certificate, CertificateStatus, GenerationJob, JobStatus, utcnow
from app.schemas.job import JobCreateRequest, RecipientInput
from app.services import certificate_service

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class RecipientCheck:
    """Outcome of validating one recipient; ``error`` is None when the recipient is valid."""

    index: int
    name: str
    email: str
    error: str | None = None


def _as_text(value: Any, max_length: int) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""


def _format_validation_error(exc: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or 'recipient'}: {error['msg']}"
        for error in exc.errors()
    ]
    return "; ".join(problems)[:MAX_ERROR_LENGTH]


def check_recipients(raw_recipients: list[Any]) -> list[RecipientCheck]:
    """Validate each recipient independently; later duplicates (same email) are rejected."""
    checks: list[RecipientCheck] = []
    first_seen_at: dict[str, int] = {}

    for index, raw in enumerate(raw_recipients):
        if not isinstance(raw, dict):
            error = "Recipient must be an object with 'name' and 'email'"
            checks.append(RecipientCheck(index, "", "", error))
            continue

        try:
            recipient = RecipientInput.model_validate(raw)
        except ValidationError as exc:
            checks.append(
                RecipientCheck(
                    index,
                    _as_text(raw.get("name"), 255),
                    _as_text(raw.get("email"), 320),
                    _format_validation_error(exc),
                )
            )
            continue

        email_key = recipient.email.lower()
        if email_key in first_seen_at:
            error = f"Duplicate recipient: email already used at index {first_seen_at[email_key]}"
            checks.append(RecipientCheck(index, recipient.name, recipient.email, error))
        else:
            first_seen_at[email_key] = index
            checks.append(RecipientCheck(index, recipient.name, recipient.email))

    return checks


def create_job(
    db: Session, request: JobCreateRequest, max_recipients: int
) -> tuple[GenerationJob, list[RecipientCheck]]:
    """Validate the request and store a PENDING job with one certificate row per recipient.

    Invalid recipients are stored as FAILED certificates (with the reason) so the outcome
    of every submitted recipient stays visible. If nobody is valid, nothing is stored.
    """
    if len(request.recipients) > max_recipients:
        raise BadRequestError(f"A job can contain at most {max_recipients} recipients")

    checks = check_recipients(request.recipients)
    rejected = [check for check in checks if check.error]
    if len(rejected) == len(checks):
        raise BadRequestError(
            "No valid recipients in the request",
            extra={
                "rejected_recipients": [
                    {"index": c.index, "name": c.name or None, "email": c.email or None, "reason": c.error}
                    for c in rejected
                ]
            },
        )

    job = GenerationJob(
        event_name=request.event_name,
        course_name=request.course_name,
        certificate_date=request.certificate_date,
        status=JobStatus.PENDING,
        total_recipients=len(checks),
        failed_count=len(rejected),
    )
    job.certificates = [
        Certificate(
            position=check.index,
            recipient_name=check.name,
            recipient_email=check.email,
            certificate_number=certificate_service.generate_certificate_number(),
            status=CertificateStatus.FAILED if check.error else CertificateStatus.PENDING,
            error_message=check.error,
        )
        for check in checks
    ]
    db.add(job)
    db.commit()
    logger.info("Created job %s (%d recipients, %d rejected)", job.id, len(checks), len(rejected))
    return job, rejected


def get_job(db: Session, job_id: str) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise NotFoundError("Generation job not found")
    return job


def list_job_certificates(db: Session, job_id: str) -> tuple[GenerationJob, list[Certificate]]:
    job = get_job(db, job_id)
    certificates = db.scalars(
        select(Certificate).where(Certificate.job_id == job_id).order_by(Certificate.position)
    ).all()
    return job, list(certificates)


def _load_pending_certificates(db: Session, job_id: str) -> list[Certificate]:
    return list(
        db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        )
    )


def _final_status(job: GenerationJob) -> JobStatus:
    if job.successful_count == 0:
        return JobStatus.FAILED
    if job.failed_count > 0:
        return JobStatus.COMPLETED_WITH_ERRORS
    return JobStatus.COMPLETED


def process_job(job_id: str, session_factory: sessionmaker[Session], settings: Settings) -> None:
    """Background task: generate every pending certificate of a job, one at a time.

    Uses its own database session because the request session is closed by then.
    Counters are committed after each certificate so clients can poll live progress.
    """
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            logger.error("Cannot process unknown job %s", job_id)
            return
        if job.status != JobStatus.PENDING:
            logger.warning("Job %s is %s, not PENDING; skipping", job_id, job.status.value)
            return

        job.status = JobStatus.PROCESSING
        job.started_at = utcnow()
        db.commit()

        try:
            # A PENDING job always has at least one pending certificate (create_job guarantees
            # one valid recipient), so the final status is set together with the last counter
            # update. Clients therefore never see 100% progress on an unfinished job.
            pending = _load_pending_certificates(db, job.id)
            for number, certificate in enumerate(pending, start=1):
                if certificate_service.generate_certificate(certificate, job, settings):
                    job.successful_count += 1
                else:
                    job.failed_count += 1
                if number == len(pending):
                    job.status = _final_status(job)
                    job.completed_at = utcnow()
                db.commit()
        except Exception:
            # Job-level problem (e.g. database error), not a single bad recipient.
            logger.exception("Job %s aborted unexpectedly", job_id)
            db.rollback()
            job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            db.commit()

        logger.info(
            "Job %s finished as %s (%d ok, %d failed)",
            job.id, job.status.value, job.successful_count, job.failed_count,
        )

"""Request/response schemas for generation jobs."""

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import GenerationJob, JobStatus
from app.utils.pdf_generator import is_text_supported


MIN_CERTIFICATE_YEAR, MAX_CERTIFICATE_YEAR = 1900, 2100
ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _plain_email(value: Any) -> Any:
    """Reject 'Name <a@b.com>' style input, which EmailStr would silently rewrite."""
    value = _strip(value)
    if isinstance(value, str) and re.search(r"[<>\s,;]", value):
        raise ValueError("Email must be a plain address such as name@example.com")
    return value


class RecipientInput(BaseModel):
    """A single recipient. Validated one by one so one bad entry does not reject the whole job."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    email: EmailStr

    _strip_name = field_validator("name", mode="before")(_strip)
    _check_email = field_validator("email", mode="before")(_plain_email)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not all(char.isprintable() for char in value):
            raise ValueError("Name contains invalid characters")
        return value


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_name": "Python Backend Workshop",
                "course_name": "Advanced Python",
                "certificate_date": "2026-10-07",
                "recipients": [
                    {"name": "John Doe", "email": "john@example.com"},
                    {"name": "Jane Smith", "email": "jane@example.com"},
                ],
            }
        }
    )

    event_name: Annotated[str, Field(min_length=1, max_length=120)]
    course_name: Annotated[str, Field(min_length=1, max_length=120)]
    certificate_date: date
    recipients: Annotated[
        list[Any],
        Field(
            min_length=1,
            description=(
                "Objects with `name` (max 100 chars) and `email`. Invalid or duplicate "
                "recipients are rejected individually; valid ones are still processed."
            ),
        ),
    ]

    _strip_whitespace = field_validator("event_name", "course_name", mode="before")(_strip)

    @field_validator("certificate_date", mode="before")
    @classmethod
    def require_iso_date_string(cls, value: Any) -> Any:
        # Without this, pydantic would also accept timestamps (0 -> 1970-01-01) and datetimes.
        if not isinstance(value, str) or not ISO_DATE_PATTERN.fullmatch(value):
            raise ValueError("certificate_date must be a date string in YYYY-MM-DD format")
        return value

    @field_validator("certificate_date")
    @classmethod
    def require_reasonable_year(cls, value: date) -> date:
        if not MIN_CERTIFICATE_YEAR <= value.year <= MAX_CERTIFICATE_YEAR:
            raise ValueError(
                f"certificate_date year must be between {MIN_CERTIFICATE_YEAR} and {MAX_CERTIFICATE_YEAR}"
            )
        return value

    @field_validator("event_name", "course_name")
    @classmethod
    def validate_certificate_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Value must not be blank")
        if not is_text_supported(value):
            raise ValueError("Value contains characters that the certificate font cannot render")
        return value


class RejectedRecipient(BaseModel):
    index: int = Field(description="Zero-based position of the recipient in the request")
    name: str | None
    email: str | None
    reason: str


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
    total_recipients: int
    rejected_recipients: list[RejectedRecipient] = Field(
        default_factory=list,
        description="Recipients that failed validation. They are stored as FAILED certificates.",
    )


class JobStatusResponse(BaseModel):
    job_id: str
    event_name: str
    course_name: str
    certificate_date: date
    status: JobStatus
    total_recipients: int
    successful_count: int
    failed_count: int
    processed_count: int
    progress_percentage: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_job(cls, job: GenerationJob) -> "JobStatusResponse":
        processed = job.successful_count + job.failed_count
        progress = round(processed / job.total_recipients * 100, 2) if job.total_recipients else 0.0
        return cls(
            job_id=job.id,
            event_name=job.event_name,
            course_name=job.course_name,
            certificate_date=job.certificate_date,
            status=job.status,
            total_recipients=job.total_recipients,
            successful_count=job.successful_count,
            failed_count=job.failed_count,
            processed_count=processed,
            progress_percentage=progress,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

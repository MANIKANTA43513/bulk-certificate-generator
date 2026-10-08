"""Database models: a GenerationJob owns many Certificates."""

import enum
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.database import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class CertificateStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


class UTCDateTime(TypeDecorator):
    """Stores timezone-aware datetimes; SQLite drops tzinfo, so restore UTC on load."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_name: Mapped[str] = mapped_column(String(120))
    course_name: Mapped[str] = mapped_column(String(120))
    certificate_date: Mapped[date] = mapped_column(Date)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=30), default=JobStatus.PENDING
    )
    total_recipients: Mapped[int] = mapped_column(Integer)
    successful_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.position"
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (UniqueConstraint("job_id", "position", name="uq_certificate_job_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # index of the recipient in the request
    recipient_name: Mapped[str] = mapped_column(String(255))
    recipient_email: Mapped[str] = mapped_column(String(320))
    certificate_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to storage dir
    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, native_enum=False, length=20), default=CertificateStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    job: Mapped[GenerationJob] = relationship(back_populates="certificates")

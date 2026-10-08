"""Generating, locating and packaging certificate files."""

import io
import logging
import zipfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.db.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.utils import pdf_generator
from app.utils.files import UnsafePathError, resolve_within, sanitize_filename

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR_MESSAGE = "Unexpected error while generating certificate"


def generate_certificate_number() -> str:
    """Random, human-friendly ID such as CERT-3F9A1C07B2D4. Uniqueness is also enforced by the DB."""
    return f"CERT-{uuid4().hex[:12].upper()}"


def download_filename(certificate: Certificate) -> str:
    return f"{sanitize_filename(certificate.recipient_name)}_{certificate.certificate_number}.pdf"


def generate_certificate(certificate: Certificate, job: GenerationJob, settings: Settings) -> bool:
    """Render one certificate and record the outcome on the model (the caller commits).

    Never raises for a single bad certificate: the failure reason is stored instead,
    so one failure cannot stop the rest of the job. Returns True on success.
    """
    relative_path = Path(job.id) / f"{certificate.id}.pdf"
    data = pdf_generator.CertificateData(
        recipient_name=certificate.recipient_name,
        course_name=job.course_name,
        event_name=job.event_name,
        certificate_date=job.certificate_date,
        certificate_number=certificate.certificate_number,
        organization_name=settings.organization_name,
    )

    try:
        destination = resolve_within(settings.storage_dir, relative_path)
        pdf_generator.render_certificate_pdf(data, destination)
    except pdf_generator.CertificateGenerationError as exc:
        return _mark_failed(certificate, str(exc))
    except (OSError, UnsafePathError):
        logger.exception("Could not write certificate %s", certificate.certificate_number)
        return _mark_failed(certificate, "Could not write the certificate file")
    except Exception:
        # Last line of defence for this one recipient only; logged with a traceback.
        logger.exception("Unexpected error for certificate %s", certificate.certificate_number)
        return _mark_failed(certificate, UNEXPECTED_ERROR_MESSAGE)

    certificate.status = CertificateStatus.GENERATED
    certificate.file_path = relative_path.as_posix()
    certificate.error_message = None
    return True


def _mark_failed(certificate: Certificate, reason: str) -> bool:
    logger.warning("Certificate %s failed: %s", certificate.certificate_number, reason)
    certificate.status = CertificateStatus.FAILED
    certificate.file_path = None
    certificate.error_message = reason[:500]
    return False


def _stored_file(certificate: Certificate, storage_dir: Path) -> Path:
    """Locate a generated file, refusing paths outside the storage directory."""
    try:
        path = resolve_within(storage_dir, certificate.file_path or "")
    except UnsafePathError:
        logger.error("Rejected unsafe file path for certificate %s", certificate.id)
        raise NotFoundError("Certificate file is no longer available") from None
    if not path.is_file():
        logger.error("Missing file for certificate %s", certificate.id)
        raise NotFoundError("Certificate file is no longer available")
    return path


def get_downloadable_certificate(
    db: Session, certificate_id: str, storage_dir: Path
) -> tuple[Certificate, Path]:
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise NotFoundError("Certificate not found")
    if certificate.status == CertificateStatus.FAILED:
        raise ConflictError(f"Certificate generation failed: {certificate.error_message}")
    if certificate.status == CertificateStatus.PENDING:
        raise ConflictError("Certificate has not been generated yet")
    return certificate, _stored_file(certificate, storage_dir)


def build_job_zip(db: Session, job: GenerationJob, storage_dir: Path) -> bytes:
    """Package every successfully generated PDF of a job into an in-memory ZIP."""
    generated = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.position)
    ).all()

    files: list[tuple[Path, str]] = []
    for certificate in generated:
        try:
            files.append((_stored_file(certificate, storage_dir), download_filename(certificate)))
        except NotFoundError:
            logger.warning("Skipping unavailable file of certificate %s in ZIP", certificate.id)

    if not files:
        if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
            raise ConflictError("No certificates are ready yet; the job is still being processed")
        raise NotFoundError("No certificates were generated for this job")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in files:
            archive.write(path, arcname=archive_name)
    return buffer.getvalue()

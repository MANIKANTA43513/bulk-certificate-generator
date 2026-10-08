"""Response schemas for certificates."""

from datetime import datetime

from pydantic import BaseModel

from app.db.models import CertificateStatus, JobStatus


class CertificateResponse(BaseModel):
    certificate_id: str
    recipient_name: str
    recipient_email: str
    certificate_number: str
    status: CertificateStatus
    error_message: str | None
    download_url: str | None
    created_at: datetime


class JobCertificatesResponse(BaseModel):
    job_id: str
    job_status: JobStatus
    zip_download_url: str | None
    certificates: list[CertificateResponse]

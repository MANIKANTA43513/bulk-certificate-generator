"""Endpoints for generation jobs and their certificates."""

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.core.errors import ErrorResponse
from app.db.models import Certificate, CertificateStatus
from app.schemas.certificate import CertificateResponse, JobCertificatesResponse
from app.schemas.job import (
    JobCreatedResponse,
    JobCreateRequest,
    JobStatusResponse,
    RejectedRecipient,
)
from app.services import certificate_service, job_service

router = APIRouter(prefix="/jobs", tags=["Jobs"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Generation job not found"}}


def _certificate_url(request: Request, certificate: Certificate) -> str | None:
    if certificate.status != CertificateStatus.GENERATED:
        return None
    return str(request.app.url_path_for("download_certificate", certificate_id=certificate.id))


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=202,
    summary="Submit a bulk certificate generation job",
    responses={400: {"model": ErrorResponse, "description": "Business validation failed"}},
)
def create_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobCreatedResponse:
    """Validate the request, store a PENDING job and return its ID immediately.

    Certificates are generated in the background. Invalid or duplicate recipients are
    rejected individually (and listed in `rejected_recipients`); the remaining valid
    recipients are still processed. If no recipient is valid the request fails with 400.
    """
    job, rejected = job_service.create_job(db, payload, settings.max_recipients_per_job)
    background_tasks.add_task(
        job_service.process_job, job.id, request.app.state.session_factory, settings
    )
    return JobCreatedResponse(
        job_id=job.id,
        status=job.status,
        total_recipients=job.total_recipients,
        rejected_recipients=[
            RejectedRecipient(
                index=check.index,
                name=check.name or None,
                email=check.email or None,
                reason=check.error or "",
            )
            for check in rejected
        ],
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status and progress",
    responses=NOT_FOUND,
)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    """`progress_percentage` is (successful + failed) / total recipients * 100."""
    return JobStatusResponse.from_job(job_service.get_job(db, job_id))


@router.get(
    "/{job_id}/certificates",
    response_model=JobCertificatesResponse,
    summary="List the certificates of a job",
    responses=NOT_FOUND,
)
def list_job_certificates(
    job_id: str, request: Request, db: Session = Depends(get_db)
) -> JobCertificatesResponse:
    """Per-recipient result: status, failure reason (if any) and download link."""
    job, certificates = job_service.list_job_certificates(db, job_id)
    items = [
        CertificateResponse(
            certificate_id=certificate.id,
            recipient_name=certificate.recipient_name,
            recipient_email=certificate.recipient_email,
            certificate_number=certificate.certificate_number,
            status=certificate.status,
            error_message=certificate.error_message,
            download_url=_certificate_url(request, certificate),
            created_at=certificate.created_at,
        )
        for certificate in certificates
    ]
    has_generated = any(item.download_url for item in items)
    zip_url = (
        str(request.app.url_path_for("download_job_certificates", job_id=job.id))
        if has_generated
        else None
    )
    return JobCertificatesResponse(
        job_id=job.id, job_status=job.status, zip_download_url=zip_url, certificates=items
    )


@router.get(
    "/{job_id}/certificates/download",
    name="download_job_certificates",
    summary="Download all generated certificates of a job as a ZIP",
    response_class=Response,
    responses={
        200: {"content": {"application/zip": {}}, "description": "ZIP of generated PDFs"},
        404: {"model": ErrorResponse, "description": "Job not found, or nothing was generated"},
        409: {"model": ErrorResponse, "description": "Job is still processing, nothing ready yet"},
    },
)
def download_job_certificates(
    job_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    job = job_service.get_job(db, job_id)
    archive = certificate_service.build_job_zip(db, job, settings.storage_dir)
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'},
    )

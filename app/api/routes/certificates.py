"""Endpoint for downloading a single certificate."""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.core.errors import ErrorResponse
from app.services import certificate_service

router = APIRouter(prefix="/certificates", tags=["Certificates"])


@router.get(
    "/{certificate_id}",
    name="download_certificate",
    summary="Download a generated certificate (PDF)",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The certificate PDF"},
        404: {"model": ErrorResponse, "description": "Certificate or its file not found"},
        409: {"model": ErrorResponse, "description": "Certificate failed or is not generated yet"},
    },
)
def download_certificate(
    certificate_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    certificate, path = certificate_service.get_downloadable_certificate(
        db, certificate_id, settings.storage_dir
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=certificate_service.download_filename(certificate),
    )

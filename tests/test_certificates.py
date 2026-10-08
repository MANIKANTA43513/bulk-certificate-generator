"""Listing, downloading (PDF and ZIP) and file-safety behaviour."""

import io
import zipfile

import pytest

from app.db.models import Certificate
from app.schemas.job import JobCreateRequest
from app.services import job_service
from app.utils.files import UnsafePathError, resolve_within, sanitize_filename


MIXED_PAYLOAD = {
    "event_name": "Python Backend Workshop",
    "course_name": "Advanced Python",
    "certificate_date": "2026-10-07",
    "recipients": [
        {"name": "John Doe", "email": "john@example.com"},
        {"name": "प्रिया शर्मा", "email": "priya@example.com"},  # fails: font cannot render it
        {"name": "Jane Smith", "email": "jane@example.com"},
    ],
}


def test_list_job_certificates(client, make_payload, submit):
    job_id = submit(make_payload(3))["job_id"]

    body = client.get(f"/api/v1/jobs/{job_id}/certificates").json()
    assert body["job_id"] == job_id
    assert body["job_status"] == "COMPLETED"
    assert body["zip_download_url"] == f"/api/v1/jobs/{job_id}/certificates/download"
    assert [c["recipient_name"] for c in body["certificates"]] == ["Person 0", "Person 1", "Person 2"]
    first = body["certificates"][0]
    assert set(first) == {
        "certificate_id", "recipient_name", "recipient_email", "certificate_number",
        "status", "error_message", "download_url", "created_at",
    }
    assert first["download_url"] == f"/api/v1/certificates/{first['certificate_id']}"


def test_download_single_certificate(client, submit):
    job_id = submit()["job_id"]
    certificate = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"][0]

    response = client.get(certificate["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert certificate["certificate_number"] in response.headers["content-disposition"]


def test_missing_certificate_returns_404(client):
    response = client.get("/api/v1/certificates/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"detail": "Certificate not found"}


def test_failed_certificate_returns_409_with_reason(client, submit):
    job_id = submit(MIXED_PAYLOAD)["job_id"]
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"][1]
    assert failed["status"] == "FAILED"
    assert failed["download_url"] is None

    response = client.get(f"/api/v1/certificates/{failed['certificate_id']}")

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Certificate generation failed:")
    assert "cannot render" in response.json()["detail"]


def test_pending_certificate_returns_409(client, db, make_payload):
    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(1)), 50)

    response = client.get(f"/api/v1/certificates/{job.certificates[0].id}")

    assert response.status_code == 409
    assert response.json() == {"detail": "Certificate has not been generated yet"}


def test_certificate_with_deleted_file_returns_404(client, settings, db, submit):
    job_id = submit()["job_id"]
    certificate = db.query(Certificate).filter_by(job_id=job_id).first()
    (settings.storage_dir / certificate.file_path).unlink()

    response = client.get(f"/api/v1/certificates/{certificate.id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Certificate file is no longer available"}


def test_tampered_file_path_outside_storage_is_never_served(client, tmp_path, db, submit):
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")
    job_id = submit()["job_id"]
    certificate = db.query(Certificate).filter_by(job_id=job_id).first()
    certificate.file_path = "../secret.txt"
    db.commit()

    response = client.get(f"/api/v1/certificates/{certificate.id}")

    assert response.status_code == 404
    assert "top secret" not in response.text


def test_missing_job_returns_404_for_listing_and_zip(client):
    missing = "00000000-0000-0000-0000-000000000000"

    for url in (f"/api/v1/jobs/{missing}/certificates", f"/api/v1/jobs/{missing}/certificates/download"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json() == {"detail": "Generation job not found"}


def test_zip_contains_every_generated_certificate(client, make_payload, submit):
    job_id = submit(make_payload(4))["job_id"]
    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"]

    response = client.get(f"/api/v1/jobs/{job_id}/certificates/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert job_id in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == 4
        for certificate in certificates:
            assert f"Person_{certificates.index(certificate)}_{certificate['certificate_number']}.pdf" in names
        assert all(archive.read(name).startswith(b"%PDF") for name in names)


def test_zip_contains_only_successful_certificates_of_a_mixed_job(client, submit):
    job_id = submit(MIXED_PAYLOAD)["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert (status["status"], status["successful_count"], status["failed_count"]) == (
        "COMPLETED_WITH_ERRORS", 2, 1,
    )

    response = client.get(f"/api/v1/jobs/{job_id}/certificates/download")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert len(names) == 2
    assert any(name.startswith("John_Doe_") for name in names)
    assert any(name.startswith("Jane_Smith_") for name in names)


def test_zip_when_nothing_was_generated_returns_404(client, make_payload, submit):
    payload = make_payload(recipients=[{"name": "प्रिया", "email": "a@example.com"}])
    job_id = submit(payload)["job_id"]

    response = client.get(f"/api/v1/jobs/{job_id}/certificates/download")

    assert response.status_code == 404
    assert response.json() == {"detail": "No certificates were generated for this job"}


def test_zip_while_job_is_pending_returns_409(client, db, make_payload):
    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(2)), 50)

    response = client.get(f"/api/v1/jobs/{job.id}/certificates/download")

    assert response.status_code == 409
    assert "still being processed" in response.json()["detail"]


def test_listing_a_job_without_generated_certificates_has_no_zip_url(client, make_payload, submit):
    payload = make_payload(recipients=[{"name": "प्रिया", "email": "a@example.com"}])
    job_id = submit(payload)["job_id"]

    assert client.get(f"/api/v1/jobs/{job_id}/certificates").json()["zip_download_url"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("John Doe", "John_Doe"),
        ("../../etc/passwd", "etc_passwd"),
        ("a/b\\c:d*e?.pdf", "a_b_c_d_e_.pdf"),
        ("José Álvarez", "Jose_Alvarez"),
        ("प्रिया", "certificate"),
        ("", "certificate"),
        ("...", "certificate"),
        ("x" * 200, "x" * 80),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_resolve_within_accepts_nested_paths_and_rejects_escapes(tmp_path):
    assert resolve_within(tmp_path, "job/cert.pdf") == (tmp_path / "job" / "cert.pdf").resolve()

    for bad in ("../outside.pdf", "job/../../outside.pdf", "/etc/passwd"):
        with pytest.raises(UnsafePathError):
            resolve_within(tmp_path, bad)


def test_zip_still_works_when_one_generated_file_was_deleted(client, settings, db, make_payload, submit):
    job_id = submit(make_payload(3))["job_id"]
    victim = db.query(Certificate).filter_by(job_id=job_id).order_by(Certificate.position).first()
    (settings.storage_dir / victim.file_path).unlink()

    response = client.get(f"/api/v1/jobs/{job_id}/certificates/download")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert not any(victim.certificate_number in name for name in archive.namelist())


def test_zip_when_every_file_was_deleted_returns_404(client, settings, db, submit):
    job_id = submit()["job_id"]
    for certificate in db.query(Certificate).filter_by(job_id=job_id):
        (settings.storage_dir / certificate.file_path).unlink()

    assert client.get(f"/api/v1/jobs/{job_id}/certificates/download").status_code == 404


@pytest.mark.parametrize(
    "bad_id",
    ["..%2f..%2fetc%2fpasswd", "../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..\\..\\windows\\win.ini"],
)
def test_path_traversal_in_ids_never_reaches_the_filesystem(client, bad_id):
    for url in (
        f"/api/v1/certificates/{bad_id}",
        f"/api/v1/jobs/{bad_id}",
        f"/api/v1/jobs/{bad_id}/certificates",
        f"/api/v1/jobs/{bad_id}/certificates/download",
    ):
        response = client.get(url)
        assert response.status_code == 404
        assert "root:" not in response.text

"""PDF generation, failure isolation and job-level outcomes."""

import re
from unittest.mock import patch


from app.db.models import Certificate, GenerationJob, JobStatus
from app.services import job_service
from app.utils import pdf_generator
from tests.conftest import pdf_text

REAL_RENDER = pdf_generator.render_certificate_pdf


def make_failing_render(failing_names: dict[str, Exception]):
    """Wrap the real renderer so chosen recipients raise the given exception."""

    def render(data, output_path):
        if data.recipient_name in failing_names:
            raise failing_names[data.recipient_name]
        REAL_RENDER(data, output_path)

    return render


def certificates_of(client, job_id: str) -> list[dict]:
    return client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"]


def test_generated_pdf_contains_certificate_details(client, make_payload, submit):
    payload = make_payload(recipients=[{"name": "John Doe", "email": "john@example.com"}])
    job_id = submit(payload)["job_id"]

    certificate = certificates_of(client, job_id)[0]
    text = pdf_text(client.get(certificate["download_url"]).content)

    assert "CERTIFICATE OF COMPLETION" in text
    assert "John Doe" in text
    assert "Advanced Python" in text
    assert "Python Backend Workshop" in text
    assert "07 October 2026" in text
    assert f"Certificate ID: {certificate['certificate_number']}" in text
    assert "TEST ACADEMY" in text  # organization name from settings


def test_certificate_number_format(client, submit):
    certificate = certificates_of(client, submit()["job_id"])[0]

    assert re.fullmatch(r"CERT-[0-9A-F]{12}", certificate["certificate_number"])


def test_each_recipient_gets_own_valid_pdf(client, make_payload, submit):
    job_id = submit(make_payload(5))["job_id"]

    certificates = certificates_of(client, job_id)
    assert len(certificates) == 5
    for index, certificate in enumerate(certificates):
        assert certificate["status"] == "GENERATED"
        assert certificate["error_message"] is None
        text = pdf_text(client.get(certificate["download_url"]).content)
        assert f"Person {index}" in text
        assert certificate["certificate_number"] in text


def test_pdfs_are_stored_under_job_directory_named_by_certificate_id(
    client, settings, db, submit
):
    job_id = submit()["job_id"]

    for certificate in db.query(Certificate).filter_by(job_id=job_id):
        assert certificate.file_path == f"{job_id}/{certificate.id}.pdf"
        assert (settings.storage_dir / certificate.file_path).is_file()
    assert not list(settings.storage_dir.rglob("*.tmp"))


def test_internal_file_paths_are_not_exposed_by_the_api(client, settings, submit):
    job_id = submit()["job_id"]

    body = client.get(f"/api/v1/jobs/{job_id}/certificates").text
    assert str(settings.storage_dir) not in body
    assert "file_path" not in body


def test_very_long_names_still_fit_on_the_page(client, make_payload, submit):
    payload = make_payload(
        course_name="C" * 120,
        recipients=[{"name": "N" * 100, "email": "long@example.com"}],
    )
    job_id = submit(payload)["job_id"]

    assert certificates_of(client, job_id)[0]["status"] == "GENERATED"


def test_unsupported_characters_fail_only_that_recipient(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "José Álvarez", "email": "jose@example.com"},
            {"name": "प्रिया शर्मा", "email": "priya@example.com"},
            {"name": "Jane Smith", "email": "jane@example.com"},
        ]
    )
    job_id = submit(payload)["job_id"]

    jose, priya, jane = certificates_of(client, job_id)
    assert (jose["status"], priya["status"], jane["status"]) == ("GENERATED", "FAILED", "GENERATED")
    assert "cannot render" in priya["error_message"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "COMPLETED_WITH_ERRORS"


def test_failure_in_the_middle_does_not_stop_remaining_certificates(
    client, make_payload, submit
):
    failing = {"Person 1": pdf_generator.CertificateGenerationError("Simulated render failure")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(make_payload(6))["job_id"]

    statuses = [c["status"] for c in certificates_of(client, job_id)]
    assert statuses == ["GENERATED", "FAILED", "GENERATED", "GENERATED", "GENERATED", "GENERATED"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "COMPLETED_WITH_ERRORS"
    assert (status["successful_count"], status["failed_count"]) == (5, 1)
    assert status["progress_percentage"] == 100.0


def test_failed_certificate_stores_reason_and_has_no_file(client, db, make_payload, submit):
    failing = {"Person 0": pdf_generator.CertificateGenerationError("Simulated render failure")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(make_payload(2))["job_id"]

    failed = db.query(Certificate).filter_by(job_id=job_id, status="FAILED").one()
    assert failed.error_message == "Simulated render failure"
    assert failed.file_path is None


def test_unexpected_exception_is_isolated_and_message_is_generic(client, make_payload, submit):
    failing = {"Person 1": RuntimeError("internal detail that must not leak")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(make_payload(3))["job_id"]

    certificates = certificates_of(client, job_id)
    assert [c["status"] for c in certificates] == ["GENERATED", "FAILED", "GENERATED"]
    assert certificates[1]["error_message"] == "Unexpected error while generating certificate"
    assert "internal detail" not in str(certificates)


def test_disk_error_is_isolated(client, make_payload, submit):
    failing = {"Person 0": OSError("disk full")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(make_payload(3))["job_id"]

    certificates = certificates_of(client, job_id)
    assert [c["status"] for c in certificates] == ["FAILED", "GENERATED", "GENERATED"]
    assert certificates[0]["error_message"] == "Could not write the certificate file"


def test_job_with_only_failures_ends_as_failed(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "प्रिया", "email": "a@example.com"},
            {"name": "राहुल", "email": "b@example.com"},
        ]
    )
    job_id = submit(payload)["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "FAILED"
    assert (status["successful_count"], status["failed_count"]) == (0, 2)
    assert status["completed_at"] is not None


def test_job_level_error_marks_job_failed(db, session_factory, settings, make_payload):
    from app.schemas.job import JobCreateRequest

    job, _ = job_service.create_job(db, JobCreateRequest(**make_payload(2)), 50)

    with patch.object(job_service, "_load_pending_certificates", side_effect=RuntimeError("db down")):
        job_service.process_job(job.id, session_factory, settings)

    with session_factory() as fresh:
        stored = fresh.get(GenerationJob, job.id)
        assert stored.status == JobStatus.FAILED
        assert stored.completed_at is not None


def test_processing_a_finished_job_again_does_nothing(client, session_factory, settings, submit):
    job_id = submit()["job_id"]

    with patch.object(pdf_generator, "render_certificate_pdf") as render:
        job_service.process_job(job_id, session_factory, settings)

    render.assert_not_called()
    assert client.get(f"/api/v1/jobs/{job_id}").json()["successful_count"] == 3


def test_processing_unknown_job_is_a_safe_noop(session_factory, settings):
    job_service.process_job("does-not-exist", session_factory, settings)


def test_ten_recipient_bulk_job_creates_ten_distinct_personalised_certificates(
    client, make_payload, submit
):
    recipients = [{"name": f"Recipient Number{i}", "email": f"r{i}@example.com"} for i in range(10)]
    job_id = submit(make_payload(recipients=recipients))["job_id"]  # ONE request

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert (status["status"], status["total_recipients"]) == ("COMPLETED", 10)
    assert (status["successful_count"], status["failed_count"], status["processed_count"]) == (10, 0, 10)
    assert status["progress_percentage"] == 100.0

    certificates = certificates_of(client, job_id)
    assert len({c["certificate_number"] for c in certificates}) == 10
    for index, certificate in enumerate(certificates):
        text = pdf_text(client.get(certificate["download_url"]).content)
        assert f"Recipient Number{index}" in text
        # No other recipient's name may appear on this certificate.
        assert not any(f"Recipient Number{other}" in text for other in range(10) if other != index)


def test_mixed_batch_of_five_with_failure_at_recipient_two_still_processes_three_to_five(
    client, make_payload, submit
):
    payload = make_payload(
        recipients=[
            {"name": "Alice A", "email": "a@example.com"},      # valid
            {"name": "Bob B", "email": "b@example.com"},        # valid input, fails during generation
            {"name": "Carol C", "email": "c@example.com"},      # valid
            {"name": "Dave D", "email": "not-an-email"},        # invalid input
            {"name": "Erin E", "email": "e@example.com"},       # valid
        ]
    )
    failing = {"Bob B": pdf_generator.CertificateGenerationError("Simulated failure for Bob")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(payload)["job_id"]

    alice, bob, carol, dave, erin = certificates_of(client, job_id)
    assert [c["status"] for c in (alice, bob, carol, dave, erin)] == [
        "GENERATED", "FAILED", "GENERATED", "FAILED", "GENERATED",
    ]
    assert bob["error_message"] == "Simulated failure for Bob"
    assert "email" in dave["error_message"]
    for generated in (alice, carol, erin):  # recipients after the failure were still processed
        assert client.get(generated["download_url"]).content.startswith(b"%PDF")

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "COMPLETED_WITH_ERRORS"
    assert (status["successful_count"], status["failed_count"], status["total_recipients"]) == (3, 2, 5)


def test_succeed_fail_succeed_records_two_successes_and_one_failure(client, make_payload, submit):
    failing = {"Person 1": pdf_generator.CertificateGenerationError("boom")}
    with patch.object(pdf_generator, "render_certificate_pdf", make_failing_render(failing)):
        job_id = submit(make_payload(3))["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert (status["successful_count"], status["failed_count"]) == (2, 1)
    assert [c["status"] for c in certificates_of(client, job_id)] == ["GENERATED", "FAILED", "GENERATED"]


def test_generation_refuses_to_write_outside_the_storage_directory(settings, tmp_path, make_payload):
    from datetime import date

    from app.db.models import CertificateStatus
    from app.services import certificate_service

    tampered_job = GenerationJob(
        id="../../escaped", event_name="E", course_name="C", certificate_date=date(2026, 10, 7)
    )
    certificate = Certificate(
        id="cert", recipient_name="A", recipient_email="a@example.com", certificate_number="CERT-X"
    )

    succeeded = certificate_service.generate_certificate(certificate, tampered_job, settings)

    assert succeeded is False
    assert certificate.status == CertificateStatus.FAILED
    assert certificate.file_path is None
    assert not list(tmp_path.rglob("cert.pdf"))
    assert not (tmp_path / "escaped").exists()

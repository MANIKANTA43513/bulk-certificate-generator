"""Request validation: request-level errors (422/400) and per-recipient rejection."""

import pytest


def post(client, payload):
    return client.post("/api/v1/jobs", json=payload)


@pytest.mark.parametrize("missing_field", ["event_name", "course_name", "certificate_date", "recipients"])
def test_missing_required_field_returns_422(client, make_payload, missing_field):
    payload = make_payload()
    del payload[missing_field]

    assert post(client, payload).status_code == 422


@pytest.mark.parametrize("field", ["event_name", "course_name"])
@pytest.mark.parametrize("value", ["", "   ", "x" * 121])
def test_blank_or_too_long_event_and_course_return_422(client, make_payload, field, value):
    assert post(client, make_payload(**{field: value})).status_code == 422


def test_event_name_with_unrenderable_characters_returns_422(client, make_payload):
    assert post(client, make_payload(event_name="पायथन कार्यशाला")).status_code == 422


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", "07/10/2026", ""])
def test_invalid_certificate_date_returns_422(client, make_payload, value):
    assert post(client, make_payload(certificate_date=value)).status_code == 422


def test_empty_recipient_list_returns_422(client, make_payload):
    response = post(client, make_payload(recipients=[]))

    assert response.status_code == 422
    assert "detail" in response.json()


def test_too_many_recipients_returns_400(client, make_payload):
    response = post(client, make_payload(51))  # test limit is 50

    assert response.status_code == 400
    assert "at most 50" in response.json()["detail"]


def test_all_recipients_invalid_returns_400_and_creates_no_job(client, make_payload):
    payload = make_payload(
        recipients=[
            {"name": "John", "email": "not-an-email"},
            {"name": "", "email": "jane@example.com"},
        ]
    )
    response = post(client, payload)

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "No valid recipients in the request"
    assert [r["index"] for r in body["rejected_recipients"]] == [0, 1]


@pytest.mark.parametrize(
    ("recipient", "expected_reason"),
    [
        ({"name": "John", "email": "invalid-email"}, "email"),
        ({"name": "John", "email": "john@"}, "email"),
        ({"name": "John"}, "email"),
        ({"email": "john@example.com"}, "name"),
        ({"name": "", "email": "john@example.com"}, "name"),
        ({"name": "   ", "email": "john@example.com"}, "name"),
        ({"name": "x" * 101, "email": "john@example.com"}, "name"),
        ({"name": "Bad\x00Name", "email": "john@example.com"}, "name"),
        ({"name": 123, "email": "john@example.com"}, "name"),
        ("just a string", "object"),
        (None, "object"),
    ],
)
def test_invalid_recipient_is_rejected_but_valid_ones_still_processed(
    client, make_payload, recipient, expected_reason
):
    payload = make_payload(
        recipients=[{"name": "Good One", "email": "good@example.com"}, recipient]
    )
    response = post(client, payload)

    assert response.status_code == 202
    body = response.json()
    assert body["total_recipients"] == 2
    assert len(body["rejected_recipients"]) == 1
    rejected = body["rejected_recipients"][0]
    assert rejected["index"] == 1
    assert expected_reason in rejected["reason"]

    status = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert status["status"] == "COMPLETED_WITH_ERRORS"
    assert (status["successful_count"], status["failed_count"]) == (1, 1)


def test_rejected_recipient_is_listed_as_failed_with_reason(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "Good One", "email": "good@example.com"},
            {"name": "Bad One", "email": "bad-email"},
        ]
    )
    job_id = submit(payload)["job_id"]

    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["certificates"]
    good, bad = certificates
    assert good["status"] == "GENERATED"
    assert bad["status"] == "FAILED"
    assert "email" in bad["error_message"]
    assert bad["download_url"] is None


def test_duplicate_email_is_rejected_case_insensitively(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "John Doe", "email": "john@example.com"},
            {"name": "Johnny", "email": "JOHN@example.com"},
            {"name": "Jane", "email": "jane@example.com"},
        ]
    )
    body = submit(payload)

    assert [r["index"] for r in body["rejected_recipients"]] == [1]
    assert "Duplicate" in body["rejected_recipients"][0]["reason"]
    status = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert status["successful_count"] == 2  # the first John and Jane
    assert status["failed_count"] == 1


def test_same_name_with_different_emails_is_allowed(client, make_payload, submit):
    payload = make_payload(
        recipients=[
            {"name": "John Doe", "email": "john1@example.com"},
            {"name": "John Doe", "email": "john2@example.com"},
        ]
    )
    body = submit(payload)

    assert body["rejected_recipients"] == []


@pytest.mark.parametrize(
    "email",
    [
        "John Doe <john@example.com>",  # EmailStr alone would silently rewrite this
        "<john@example.com>",
        "john@example.com, jane@example.com",
        "john@example.com;jane@example.com",
        "john doe@example.com",
        "john@@example.com",
        "@example.com",
        "john@",
        "john@example",
        "john",
        "",
        "a" * 250 + "@example.com",  # longer than the 254-character limit
    ],
)
def test_malformed_or_oversized_emails_are_rejected(client, make_payload, email):
    payload = make_payload(
        recipients=[{"name": "Good", "email": "good@example.com"}, {"name": "Bad", "email": email}]
    )
    response = post(client, payload)

    assert response.status_code == 202
    rejected = response.json()["rejected_recipients"]
    assert [r["index"] for r in rejected] == [1]
    assert "mail" in rejected[0]["reason"].lower()


@pytest.mark.parametrize(
    "value",
    [None, 0, 86400, 20261007, True, "", "2026-02-30", "2026-1-1", "07-10-2026",
     "2026-10-07T00:00:00", "2026-10-07T10:30:00", "0001-01-01", "9999-12-31", "1899-12-31"],
)
def test_malformed_or_unreasonable_dates_return_422(client, make_payload, value):
    assert post(client, make_payload(certificate_date=value)).status_code == 422


@pytest.mark.parametrize("value", ["1900-01-01", "2026-10-07", "2100-12-31"])
def test_valid_dates_are_accepted(client, make_payload, value):
    assert post(client, make_payload(certificate_date=value)).status_code == 202


def test_validation_message_is_clear_for_a_bad_date(client, make_payload):
    body = post(client, make_payload(certificate_date=0)).json()

    assert "YYYY-MM-DD" in body["detail"][0]["msg"]

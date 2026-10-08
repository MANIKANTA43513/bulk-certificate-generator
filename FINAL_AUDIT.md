# Final Pre-Submission Audit

Status legend: **PASS** = verified as-is, **FIXED** = a defect was found by this audit and corrected (then re-verified), **FAIL** = not met.

Result: **143 tests, 143 passed, 0 failed** (Python 3.11.17 and 3.12; also from a fresh virtualenv and from the extracted ZIP).

## 1. Defects found and fixed during this audit

| # | Finding | How it was found | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | `John Doe <john@example.com>` was **silently accepted** and rewritten to `john@example.com` (Pydantic `EmailStr` behaviour) | Probing validation directly | Emails containing `<`, `>`, whitespace, `,` or `;` are rejected | `test_malformed_or_oversized_emails_are_rejected` |
| 2 | `certificate_date` accepted `0` (-> 1970-01-01), timestamps, date-time strings, years like 0001 / 9999 | Probing validation directly | Must be a `YYYY-MM-DD` string, year 1900-2100 | `test_malformed_or_unreasonable_dates_return_422`, `test_valid_dates_are_accepted` |
| 3 | If one generated PDF was missing on disk, the **whole ZIP download failed (404)** | Code review of `build_job_zip` | Missing files are skipped with a warning; 404 only if nothing is available | `test_zip_still_works_when_one_generated_file_was_deleted`, `test_zip_when_every_file_was_deleted_returns_404` |
| 3b | (Found in the earlier build round) `PROCESSING` + 100% could be visible for an instant because counters and final status were committed separately | Live polling | Final status is committed together with the last counter update | `test_progress_never_reaches_100_percent_before_job_is_finished`, `test_polling_a_running_job_never_shows_an_impossible_state` |
| 4 | Plain `pytest` from a repo that contains a virtualenv folder could collect third-party tests | Planning the clean-install audit | Added `pytest.ini` (`testpaths = tests`, `pythonpath = .`) | Verified: plain `pytest` in a repo containing `fresh_venv/` ran exactly 143 tests |
| 5 | `INTERVIEW_NOTES.md` lacked dedicated "Why SQLAlchemy / Why ReportLab" answers and a future production architecture section; test counts were stale | Documentation audit | Added; counts updated | n/a (reviewed) |
| 6 | README did not document the strict email/date rules, ZIP skip behaviour, non-paginated listing, orphaned files on direct DB deletion, or CWD-relative paths in `.env` | Documentation audit | Added | n/a (README claims machine-checked, see section 4) |

## 2. Requirement checklist

### Core functional requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| Accept certificate generation requests | PASS | `POST /api/v1/jobs` -> 202; live run with the README sample (5 recipients) |
| Request has event/course info + recipients list | PASS | `JobCreateRequest` (event_name, course_name, certificate_date, recipients); missing field -> 422 (live + `test_missing_required_field_returns_422`) |
| Validate recipient data | PASS | Per-recipient validation; live matrix: blank name, missing email, bad/display-name email, 101-char name, duplicate, non-object all rejected individually |
| One certificate per valid recipient | PASS | Live: 10 recipients -> 10 GENERATED, 10 distinct PDFs; `test_ten_recipient_bulk_job_creates_ten_distinct_personalised_certificates` |
| ONE predefined template | PASS | Single `_draw_template` in `app/utils/pdf_generator.py`; PDF rendered to PNG and inspected visually |
| Recipient-specific information | PASS | Live: each of 10 PDFs contains its own name and certificate number and none of the other 9 names (checked with `pdftotext`) |
| Bulk support (many recipients, one request) | PASS | One POST created 10-, 30- and 400-recipient jobs |
| Track generation status | PASS | `GET /jobs/{id}`; statuses PENDING / PROCESSING / COMPLETED / COMPLETED_WITH_ERRORS / FAILED |
| Check total, successful, failed, status, progress | PASS | Response has `total_recipients`, `successful_count`, `failed_count`, `processed_count`, `progress_percentage`, `status`, timestamps; verified live |
| One failure must not stop the rest | PASS | Live mixed batch: failure at #2 -> #3, #4(invalid), #5 handled; `test_mixed_batch_of_five_with_failure_at_recipient_two_still_processes_three_to_five` |
| Job result identifies successes, failures, reasons | PASS | `GET /jobs/{id}/certificates` shows status + `error_message` per recipient (live) |
| Generated certificates retrievable | PASS | Single PDF (`curl -OJ`), listing, ZIP all verified live |
| Automated tests for all important functionality | PASS | 143 tests, see section 3 |

### Bulk processing design

| Requirement | Status | Evidence |
| --- | --- | --- |
| FastAPI BackgroundTasks | PASS | `background_tasks.add_task(job_service.process_job, ...)` in `routes/jobs.py`; POST returns `PENDING` before work finishes (live) |
| Flow: validate -> create job -> return job_id -> background -> poll -> retrieve | PASS | Live run of exactly this flow on a fresh install |
| Sensible status transitions | PASS | Only forward: PENDING -> PROCESSING -> COMPLETED / COMPLETED_WITH_ERRORS / FAILED; `process_job` skips any job not PENDING (`test_processing_a_finished_job_again_does_nothing`) |
| No distributed task queue | PASS | No Redis/Celery in `requirements.txt` |

### Database

| Requirement | Status | Evidence |
| --- | --- | --- |
| SQLite + SQLAlchemy, clean schema | PASS | `app/db/models.py`: `generation_jobs`, `certificates` |
| Primary keys (UUID strings) | PASS | `new_uuid` default; `test_defaults_are_applied_on_insert` |
| Foreign key + relationship job -> certificates | PASS | `ForeignKey(..., ondelete="CASCADE")`, `relationship(..., order_by=position)`; `PRAGMA foreign_keys=ON`; `test_certificate_cannot_reference_a_missing_job` (fails if the pragma is removed - mutation-checked) |
| Unique certificate numbers | PASS | `unique=True` + `test_certificate_numbers_must_be_unique`; 20 numbers unique across 2 jobs |
| Unique recipient position per job | PASS | `UniqueConstraint(job_id, position)` + test |
| NOT NULL on required fields | PASS | `test_required_columns_reject_null` |
| Timestamps (created/started/completed), UTC-aware | PASS | Live: `created_at <= started_at <= completed_at`; `UTCDateTime` type |
| Deleting records cannot break relationships | PASS | Cascade tested via ORM and raw SQL; other jobs unaffected (`test_deleting_a_job_removes_its_certificates_and_leaves_other_jobs_alone`, `test_database_level_cascade_works_without_the_orm`) |
| Indexes where useful | PASS | `job_id` and `certificate_number` indexed |

### Input validation

| Requirement | Status | Evidence |
| --- | --- | --- |
| event_name / course_name required, trimmed, max length | PASS | 422 for missing, blank, 121 chars (live + parametrized tests) |
| certificate_date valid | FIXED | Strict `YYYY-MM-DD`, year 1900-2100 (finding 2); live: `2026-02-30` and `0` -> 422 |
| recipients required, non-empty | PASS | Live: `[]` -> 422, missing -> 422 |
| recipient name non-empty / trimmed / max length | PASS | Blank, 101 chars, control characters rejected; whitespace collapsed (`test_whitespace_is_trimmed_and_collapsed`) |
| Valid, well-formed email | FIXED | Finding 1; 12 malformed variants tested incl. >254 chars |
| Duplicate handling | PASS | Case-insensitive duplicate email: first kept, later rejected with the original index (live + test) |
| No silent generation for invalid data | PASS | Invalid recipients stored as FAILED with reason, listed in `rejected_recipients`; all-invalid -> 400, nothing created |
| Partial processing documented | PASS | README "Input validation behaviour" table |

### Certificate PDF

| Requirement | Status | Evidence |
| --- | --- | --- |
| Title, recipient, course/event, date, certificate number, organization | PASS | `pdftotext` on live PDFs; `test_generated_pdf_contains_certificate_details` (via `pypdf`) |
| Valid, 1-page A4 landscape PDF | PASS | `pdfinfo`: 1 page, 841.89 x 595.276 pts, PDF 1.4; ZIP `unzip -t` clean |
| Professional layout | PASS | Rendered PNG inspected visually |
| Stored under `storage/certificates/` | PASS | `<job_id>/<certificate_id>.pdf`; `test_pdfs_are_stored_under_job_directory_named_by_certificate_id` |
| Not committed to Git | PASS | `.gitignore` verified with simulated runtime files; none appear in `git add -A -n` |
| Atomic writes | PASS | temp file + `os.replace`; test asserts no `*.tmp` left |

### Retrieval and API

| Requirement | Status | Evidence |
| --- | --- | --- |
| `POST /api/v1/jobs` -> 202 `{job_id,status,total_recipients}` | PASS | Live |
| `GET /api/v1/jobs/{id}` -> all documented fields | PASS | Live; README example keys machine-compared with the response model |
| `GET /api/v1/jobs/{id}/certificates` | PASS | Live; includes id, name, email, number, status, error, download URL |
| `GET /api/v1/certificates/{id}` -> PDF | PASS | Live: 200, `application/pdf`, `%PDF-` header, filename `Priya_Sharma_CERT-....pdf` |
| Certificate failed -> appropriate error | PASS | 409 `Certificate generation failed: <reason>` (live + test) |
| `GET /api/v1/jobs/{id}/certificates/download` (ZIP) | PASS | Live: valid ZIP, 10/10 and 3/3 PDFs; failed ones excluded; names like `John_Doe_CERT-5CE1120F9C81.pdf` |
| ZIP with nothing generated -> meaningful response | PASS | 404 "No certificates were generated for this job"; 409 while still processing |
| Wrong HTTP methods | PASS | 405 for 8 method/URL combinations |
| Swagger `/docs` and ReDoc `/redoc` | PASS | HTTP 200 on the fresh install and in tests; all 5 paths in `openapi.json` |

### Error handling

| Requirement | Status | Evidence |
| --- | --- | --- |
| 404 missing job / certificate | PASS | Live + tests, JSON `{"detail": ...}` |
| 400 business validation | PASS | All-invalid recipients; >1000 recipients |
| 409 only for genuine state conflicts | PASS | Failed/pending certificate, ZIP while processing |
| 422 schema validation | PASS | Live + tests; malformed JSON body -> 422 |
| 500 hides internals | PASS | Forced a real 500 by dropping a table under the running server: body was exactly `{"detail":"Internal server error"}`; traceback appears only in the server log |
| Per-recipient failure reasons stored | PASS | `certificates.error_message`; shown in listing |

### Failure isolation (implementation inspected, not just tests)

| Requirement | Status | Evidence |
| --- | --- | --- |
| Handled per recipient | PASS | `certificate_service.generate_certificate` has its own `try` per certificate; returns bool, never raises |
| A succeeds, B fails, C succeeds -> 2 ok / 1 failed | PASS | `test_succeed_fail_succeed_records_two_successes_and_one_failure` |
| No broad handler silently hides failures | PASS | Only two broad `except Exception`: per-recipient (logs traceback + stores generic reason) and job-level in `process_job` (logs, marks job FAILED). Grepped all `except` in `app/` |
| Reasons persisted and exposed | PASS | Domain error -> specific message; OSError -> "Could not write the certificate file"; unexpected -> generic message (internals not leaked, tested) |
| Mutation check | PASS | Making unexpected errors propagate makes isolation tests fail |

### Status / progress consistency

| Requirement | Status | Evidence |
| --- | --- | --- |
| Valid state transitions | PASS | See bulk design table |
| Progress = (success + failed) / total * 100, 2 decimals | PASS | `test_progress_percentage_is_rounded_to_two_decimals` (33.33), live values 44.5, 65.75, ... |
| Never `PROCESSING` + 100% | FIXED | Final status + `completed_at` committed with the last counter; 685 consecutive live polls on a 400-recipient job and a threaded test found no impossible state (counters monotonic, `processed == success + failed`, `completed_at` set only when final) |
| Live progress visible | PASS | Live polls: 0.25% -> 21.75% -> 44.5% -> 65.75% -> 88.75% -> 100% COMPLETED |
| Note: `PENDING` job with rejected recipients shows non-zero progress | PASS (documented) | Rejected recipients count as processed from creation; README "Job status lifecycle" explains it |

### Security

| Requirement | Status | Evidence |
| --- | --- | --- |
| No hardcoded secrets | PASS | grep for password/secret/token/api_key in `app/`: none; no auth system |
| `.env` ignored, `.env.example` present and valid | PASS | `.gitignore`; `test_env_example_is_a_valid_configuration` |
| Path traversal protection | PASS | `resolve_within`; traversal IDs in all URL positions -> 404; tampered DB `file_path` never served; generation refuses to write outside storage (`test_generation_refuses_to_write_outside_the_storage_directory`); mutation-checked |
| Filenames sanitized, never client-derived on disk | PASS | Disk names are UUIDs; download/ZIP names pass `sanitize_filename` (8 parametrized cases) |
| No internal paths exposed | PASS | `test_internal_file_paths_are_not_exposed_by_the_api` |
| No SQL string building | PASS | grep for `text(`/`execute(`/SQL f-strings in `app/`: none |
| Files cannot overwrite arbitrary files | PASS | Server-generated UUID paths + `resolve_within` before every write |
| No unnecessary dependencies | PASS | Each non-obvious dependency uninstalled in a scratch env: the project broke every time (email-validator, httpx2, pypdf, pydantic-settings, reportlab, uvicorn) |

### Required test coverage (original minimum list)

| Required test | Status | Where |
| --- | --- | --- |
| Create job | PASS | `test_jobs.py::test_create_job_returns_202_with_job_id` |
| Input validation / empty list / invalid email | PASS | `test_validation.py` (empty list, 12 email variants, 14 date variants) |
| Certificate generation, multiple recipients | PASS | `test_generation.py` (PDF contents, 5 and 10 recipients) |
| Status/progress, successful completion | PASS | `test_status.py` |
| Individual failure, failure isolation, mixed success/failure job | PASS | `test_generation.py` |
| Retrieve certificate | PASS | `test_certificates.py::test_download_single_certificate` |
| Missing job / missing certificate -> 404 | PASS | `test_status.py`, `test_certificates.py` |
| Download generated certificates (ZIP) | PASS | `test_certificates.py` (full, mixed, empty, pending, deleted file) |
| Path traversal / security | PASS | `test_certificates.py`, `test_generation.py` |
| Not fake tests | PASS | Mutation-checked: removing each protection (isolation, final-status logic, path guard, email/date checks, ZIP skip, FK pragma) makes tests fail |

### Documentation, structure, repository

| Requirement | Status | Evidence |
| --- | --- | --- |
| README has all required sections | FIXED | Updated for current behaviour (finding 6); endpoints, env vars, example JSON keys and file tree machine-checked against code; 143 / 3.11 claims verified |
| Design decisions (10 items), BackgroundTasks reasoning, honest limitations | PASS | README "Design decisions" and "Limitations" |
| INTERVIEW_NOTES.md (60s/2min, 15+ questions) | FIXED | Now 25+ questions, "why" answers for FastAPI/SQLite/SQLAlchemy/ReportLab/BackgroundTasks, crash, large batches, PostgreSQL, future production architecture (finding 5) |
| Project structure matches the requested layout | PASS | Same tree plus `api/deps.py`, `core/errors.py`, `utils/files.py`, `pytest.ini`, `tests/test_database.py` (documented in README) |
| `storage/` placeholder | PASS | `storage/certificates/.gitkeep` (the layout in the assignment); `storage/` is created implicitly |
| Type hints / PEP8 / no dead code / no TODOs | PASS | Every function annotated (AST check); `pycodestyle --max-line-length=120`: 0 findings; `pyflakes` clean; `vulture`: only the `dialect` parameter required by SQLAlchemy's `TypeDecorator` |
| Python 3.11+ | PASS | Full suite run on 3.11.17 and 3.12 |
| `requirements.txt` minimal and installable | PASS | Fresh venv install of only this file; versions unchanged |
| Repository clean | PASS | No venv, caches, PDFs, ZIPs, DB, `.env`, IDE files, logs (listing in the final report) |
| Fresh-install audit | PASS | `python3 -m venv fresh_venv` in the project root, `pip install -r requirements.txt`, plain `pytest` (143 passed), `uvicorn app.main:app` with defaults, `/docs` 200, `/redoc` 200, full workflow on the README sample; `fresh_venv` then deleted |
| Final ZIP extracted into an empty directory and tested | PASS | See final report |

## 3. Remaining limitations (not defects; documented in README)

- `BackgroundTasks` is in-process: a server crash leaves a job in `PROCESSING` (no automatic recovery).
- Sequential generation; SQLite single writer.
- Built-in PDF fonts: Latin script only (other scripts fail per recipient with a clear message).
- ZIP built in memory; certificate listing not paginated (both bounded by the 1000-recipient limit).
- No authentication, rate limiting, file retention or migrations.
- Python 3.11 and 3.12 were tested; other versions were not.

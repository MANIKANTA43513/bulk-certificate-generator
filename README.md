# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients** and generates a PDF certificate for each of them in the background, while letting the client track progress and download the results.

Built with FastAPI, SQLAlchemy (SQLite), Pydantic and ReportLab. Tested with pytest (143 tests, verified on Python 3.11 and 3.12).

## Table of contents

1. [Problem statement](#problem-statement)
2. [Features](#features)
3. [Technology stack](#technology-stack)
4. [Architecture](#architecture)
5. [Project structure](#project-structure)
6. [Setup](#setup)
7. [Running the application](#running-the-application)
8. [Running the tests](#running-the-tests)
9. [API documentation](#api-documentation)
10. [Example requests and responses](#example-requests-and-responses)
11. [Bulk generation flow](#bulk-generation-flow)
12. [Certificate generation approach](#certificate-generation-approach)
13. [Job status lifecycle](#job-status-lifecycle)
14. [Failure handling](#failure-handling)
15. [Input validation behaviour](#input-validation-behaviour)
16. [Design decisions](#design-decisions)
17. [Database design](#database-design)
18. [Certificate storage strategy](#certificate-storage-strategy)
19. [Error handling](#error-handling)
20. [Limitations and future improvements](#limitations-and-future-improvements)
21. [Example curl commands](#example-curl-commands)

## Problem statement

Organizers of workshops and courses need to issue certificates to many participants at once. Doing this with one API call per certificate is slow and awkward for the client. This service lets a client:

1. submit a bulk request (event/course details + a list of recipients),
2. get a `job_id` back immediately,
3. poll the job to see progress, successes and failures,
4. download each generated PDF, or all of them as a ZIP.

A problem with one recipient must never stop the others from being processed.

## Features

- Bulk submission: many recipients in a single `POST`.
- Per-recipient validation with **partial processing** (valid recipients are still generated; invalid ones are reported with a reason).
- Background generation with FastAPI `BackgroundTasks`; the API answers `202 Accepted` immediately.
- Live job status: total / successful / failed / processed counts, progress percentage, timestamps.
- Failure isolation: every certificate is generated independently and its failure reason is stored.
- One professional landscape A4 PDF template (ReportLab).
- Download a single PDF or a ZIP of all successful PDFs for a job.
- Unique, human-friendly certificate numbers (`CERT-3F9A1C07B2D4`).
- Safe file handling (sanitized filenames, path-traversal protection, no internal paths exposed).
- Strict input validation (plain email addresses only, ISO dates only, length limits, duplicate detection).
- Consistent JSON errors, logging, no stack traces leaked to clients.
- Swagger UI (`/docs`) and ReDoc (`/redoc`).

## Technology stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Web framework | FastAPI (served by Uvicorn) |
| Database / ORM | SQLite via SQLAlchemy 2.x |
| Validation / settings | Pydantic v2, pydantic-settings (`EmailStr` uses `email-validator`) |
| PDF generation | ReportLab |
| Tests | pytest, FastAPI `TestClient` (needs `httpx2`), `pypdf` to read generated PDFs |

No Redis, Celery, Docker, auth or frontend: the assignment does not need them.

## Architecture

```
            +-----------------+
 Client --> |  FastAPI routes |   app/api/routes   (HTTP only: parse, call service, shape response)
            +--------+--------+
                     |
            +--------v--------+
            |    Services     |   app/services     (business logic: validation, job processing, ZIP, lookups)
            +----+-------+----+
                 |       |
        +--------v--+  +-v------------------+
        | SQLAlchemy|  | utils/pdf_generator|   ReportLab template + safe file helpers
        |  (SQLite) |  | utils/files        |
        +-----------+  +--------------------+
```

- **Routes** contain no business logic.
- **Services** hold the logic and raise small domain errors (`NotFoundError`, `ConflictError`, `BadRequestError`) that a single handler turns into JSON.
- **`create_app(settings)`** is an application factory. Each app instance owns its own database engine and settings, which is what lets every test run against a throwaway database and storage directory.

## Project structure

```
bulk-certificate-generator/
├── app/
│   ├── main.py                    # app factory, router registration, lifespan (creates tables)
│   ├── api/
│   │   ├── deps.py                # get_db / get_settings dependencies
│   │   └── routes/
│   │       ├── jobs.py            # POST /jobs, GET /jobs/{id}, certificates list, ZIP download
│   │       └── certificates.py    # GET /certificates/{id}
│   ├── core/
│   │   ├── config.py              # Settings (env vars / .env)
│   │   └── errors.py              # AppError classes + JSON handlers
│   ├── db/
│   │   ├── database.py            # engine, session factory, init_db
│   │   └── models.py              # GenerationJob, Certificate, enums
│   ├── schemas/
│   │   ├── job.py                 # request/response models for jobs
│   │   └── certificate.py         # response models for certificates
│   ├── services/
│   │   ├── job_service.py         # validation, create_job, process_job (background task)
│   │   └── certificate_service.py # generate one certificate, locate files, build ZIP
│   └── utils/
│       ├── pdf_generator.py       # the certificate template
│       └── files.py               # sanitize_filename, resolve_within (path safety)
├── tests/                         # conftest + test_jobs/validation/generation/status/certificates/database
├── storage/certificates/.gitkeep  # generated PDFs are written here (git-ignored)
├── pytest.ini                     # test discovery (tests/ only)
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── INTERVIEW_NOTES.md
└── FINAL_AUDIT.md                 # requirement-by-requirement audit with evidence
```

Two small additions to the suggested layout: `api/deps.py` (dependencies) and `core/errors.py` + `utils/files.py` (error handling and path safety), each of which keeps another file focused.

## Setup

Requires **Python 3.11+** (test suite verified on 3.11.17 and 3.12).

```bash
# 1. Virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Environment configuration (optional - defaults work out of the box)
cp .env.example .env
```

### Environment configuration

All settings are optional and read from environment variables or `.env` (never committed). The `.env` file is always read from the project root; relative paths *inside* it (as in `.env.example`) resolve from the directory you start the server in, so start it from the project root or use absolute paths.

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `Bulk Certificate Generator` | Title shown in Swagger |
| `DATABASE_URL` | `sqlite:///<project>/storage/app.db` | SQLAlchemy URL |
| `STORAGE_DIR` | `<project>/storage/certificates` | Where PDFs are written |
| `ORGANIZATION_NAME` | `Example Learning Academy` | Provider name printed on certificates |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Upper limit per request (400 above it) |
| `LOG_LEVEL` | `INFO` | Python logging level |

### Database setup

There is nothing to run. Tables are created automatically on application startup (`init_db`), and the SQLite file is created at `storage/app.db`. To start fresh, stop the server and delete `storage/app.db*`.

## Running the application

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs>.

## Running the tests

```bash
pytest
```

Every test gets its own temporary SQLite database and certificate directory (see `tests/conftest.py`), so tests never touch real data. FastAPI's `TestClient` runs background tasks before it returns the response, so after a `POST /jobs` call in a test the job is already finished.

What the 143 tests cover:

| File | Covers |
| --- | --- |
| `test_jobs.py` | job creation (202), persistence, 30-recipient bulk job, trimming, unique certificate numbers, clean 500, wrong HTTP methods (405), malformed JSON, `.env.example` validity, `/docs` and `/redoc` |
| `test_validation.py` | missing/blank/too-long fields, malformed/unreasonable dates, malformed and oversized emails (incl. `Name <a@b.com>`), empty list, too many recipients, all-invalid (400), partial rejection, duplicates |
| `test_generation.py` | PDF content, 10-recipient bulk job with per-recipient personalisation, 5-recipient mixed batch (failure at #2 does not stop #3-#5), succeed/fail/succeed counters, failure isolation (domain, unexpected and disk errors), refusal to write outside storage, all-failed job, job-level failure, idempotent processing |
| `test_status.py` | status fields, pending state, **live progress while processing**, progress rounding, no premature 100%, **polling a running job from another thread never shows an impossible state**, 404 |
| `test_certificates.py` | listing, PDF download, 404/409 cases, missing/tampered files, ZIP (full, mixed, empty, pending, one file deleted), filename sanitizing, path-traversal guard and traversal-style IDs in URLs |
| `test_database.py` | foreign keys, uniqueness (certificate number, position per job), NOT NULL, cascade delete (ORM and raw SQL), ordering |

## API documentation

Interactive docs: `/docs` (Swagger UI) and `/redoc`. Raw schema: `/openapi.json`.

| Method | Path | Purpose | Success |
| --- | --- | --- | --- |
| `POST` | `/api/v1/jobs` | Submit a bulk generation job | `202` |
| `GET` | `/api/v1/jobs/{job_id}` | Job status and progress | `200` |
| `GET` | `/api/v1/jobs/{job_id}/certificates` | Per-recipient results with download links | `200` |
| `GET` | `/api/v1/jobs/{job_id}/certificates/download` | ZIP of all generated PDFs | `200` |
| `GET` | `/api/v1/certificates/{certificate_id}` | Download one PDF | `200` |

Error statuses: `400` business validation, `404` unknown job/certificate, `409` conflicting state (certificate failed/not ready, job still processing and nothing ready for ZIP), `422` request-schema validation, `500` unexpected.

## Example requests and responses

### Create a job: `POST /api/v1/jobs`

```json
{
  "event_name": "Python Backend Workshop",
  "course_name": "Advanced Python",
  "certificate_date": "2026-10-07",
  "recipients": [
    { "name": "John Doe", "email": "john@example.com" },
    { "name": "Jane Smith", "email": "jane@example.com" },
    { "name": "Alex Johnson", "email": "alex@example.com" },
    { "name": "Priya Sharma", "email": "priya@example.com" },
    { "name": "Rahul Kumar", "email": "rahul@example.com" }
  ]
}
```

Response `202 Accepted`:

```json
{
  "job_id": "986eb089-5f8c-4bd8-a17c-21199cf71970",
  "status": "PENDING",
  "total_recipients": 5,
  "rejected_recipients": []
}
```

With some invalid recipients (valid ones are still processed):

```json
{
  "job_id": "5b0c...",
  "status": "PENDING",
  "total_recipients": 6,
  "rejected_recipients": [
    { "index": 3, "name": "Cy", "email": "nope",
      "reason": "email: value is not a valid email address: An email address must have an @-sign." },
    { "index": 4, "name": "Dup", "email": "ANN@example.com",
      "reason": "Duplicate recipient: email already used at index 0" }
  ]
}
```

### Poll status: `GET /api/v1/jobs/{job_id}`

```json
{
  "job_id": "986eb089-5f8c-4bd8-a17c-21199cf71970",
  "event_name": "Python Backend Workshop",
  "course_name": "Advanced Python",
  "certificate_date": "2026-10-07",
  "status": "PROCESSING",
  "total_recipients": 400,
  "successful_count": 178,
  "failed_count": 0,
  "processed_count": 178,
  "progress_percentage": 44.5,
  "created_at": "2026-10-07T09:36:42.308168Z",
  "started_at": "2026-10-07T09:36:42.320241Z",
  "completed_at": null
}
```

### List certificates: `GET /api/v1/jobs/{job_id}/certificates`

```json
{
  "job_id": "5b0c...",
  "job_status": "COMPLETED_WITH_ERRORS",
  "zip_download_url": "/api/v1/jobs/5b0c.../certificates/download",
  "certificates": [
    {
      "certificate_id": "584a4be9-7203-43af-8c9d-e71b1cb89639",
      "recipient_name": "Ann",
      "recipient_email": "ann@example.com",
      "certificate_number": "CERT-F1D6EAF3F02A",
      "status": "GENERATED",
      "error_message": null,
      "download_url": "/api/v1/certificates/584a4be9-7203-43af-8c9d-e71b1cb89639",
      "created_at": "2026-10-07T09:38:01.120000Z"
    },
    {
      "certificate_id": "7e41...",
      "recipient_name": "प्रिया",
      "recipient_email": "p@example.com",
      "certificate_number": "CERT-9A02B7C4D1E3",
      "status": "FAILED",
      "error_message": "Recipient name contains characters that the certificate font cannot render",
      "download_url": null,
      "created_at": "2026-10-07T09:38:01.120000Z"
    }
  ]
}
```

### Error examples

```json
{ "detail": "Generation job not found" }
```

```json
{ "detail": "Certificate generation failed: Recipient name contains characters that the certificate font cannot render" }
```

```json
{ "detail": "No valid recipients in the request",
  "rejected_recipients": [ { "index": 0, "name": "A", "email": "bad", "reason": "email: ..." } ] }
```

## Bulk generation flow

```
POST /api/v1/jobs
        |
        v
Pydantic validates the request shape (event, course, date, non-empty recipients)   -> 422 on failure
        |
        v
Each recipient is validated independently; duplicates (same email) are detected
        |   all invalid? -> 400, nothing stored
        v
Job (PENDING) + one Certificate row per recipient are stored in ONE transaction
        |   valid recipients -> PENDING, invalid ones -> FAILED with a reason
        v
202 Accepted { job_id, status, total_recipients, rejected_recipients }
        |
        v   (after the response is sent)
Background task process_job(job_id)
        |   job -> PROCESSING
        +-- recipient 1 -> GENERATED   (commit counters)
        +-- recipient 2 -> GENERATED   (commit counters)
        +-- recipient 3 -> FAILED      (reason stored, commit counters)
        +-- recipient 4 -> GENERATED   (commit counters + final job status on the last one)
        v
Client polls GET /jobs/{id}, then downloads PDFs or the ZIP
```

## Certificate generation approach

`app/utils/pdf_generator.py` draws one predefined landscape A4 template with ReportLab's canvas: double border, organization name, "CERTIFICATE OF COMPLETION", recipient name, course, event, date (`07 October 2026`), certificate ID and a signature line. Long text automatically shrinks to fit the page.

Details worth knowing:

- Text is rendered with ReportLab's built-in fonts (Helvetica/Times), which support Western-European characters (`José`, `Müller`) but **not** scripts such as Devanagari. Those fonts silently draw black boxes for unsupported characters, so the generator checks the text first and fails that certificate with a clear message instead of producing a broken PDF.
- The same check rejects unrenderable `event_name`/`course_name` with `422` up front, because they would fail every certificate.
- A PDF is written to `<name>.pdf.tmp` and then renamed (`os.replace`), so a crash never leaves a half-written certificate behind.

## Job status lifecycle

```
PENDING --> PROCESSING --> COMPLETED               every certificate generated
                      |--> COMPLETED_WITH_ERRORS   at least one success AND at least one failure
                      |--> FAILED                  zero successes, or an unexpected job-level error
```

- Transitions only move forward. `process_job` refuses to start unless the job is `PENDING`, so a job can never be processed twice.
- Recipients rejected during validation count as failures (and as processed) from the start, so `COMPLETED_WITH_ERRORS` also covers "some recipients were invalid". A freshly created job with rejected recipients therefore reports a non-zero `failed_count` and `progress_percentage` while still `PENDING`; progress can never reach 100% before the job is finished because at least one valid recipient is always still pending.
- `completed_at` is set in the same database commit as the final status, so a client never sees 100% progress on an unfinished job (there is a regression test for this).

**Progress** = `(successful_count + failed_count) / total_recipients * 100`, rounded to 2 decimals. The counters are committed after every certificate, so polling shows real progress.

## Failure handling

Each recipient is generated inside its own `try` block (`certificate_service.generate_certificate`):

| What goes wrong | What happens |
| --- | --- |
| Predictable rendering problem (e.g. unsupported characters) | Certificate marked `FAILED` with the specific reason |
| Disk problem (`OSError`) | Marked `FAILED`: "Could not write the certificate file" (details only in the log) |
| Anything unexpected | Logged with a traceback, marked `FAILED`: "Unexpected error while generating certificate" (no internals leaked) |

In every case the loop continues with the next recipient. A separate outer handler in `process_job` only catches *job-level* problems (e.g. the database becoming unavailable); it logs and sets the job to `FAILED`, so a job is never left in `PROCESSING` after a handled error.

## Input validation behaviour

Chosen behaviour: **partial processing**.

| Problem | Result |
| --- | --- |
| Missing/blank/too-long `event_name` or `course_name`, bad `certificate_date`, missing or empty `recipients` | `422`, nothing created |
| More recipients than `MAX_RECIPIENTS_PER_JOB` | `400`, nothing created |
| **Every** recipient invalid | `400` with `rejected_recipients`, nothing created |
| **Some** recipients invalid (bad/missing email, blank or too-long name, wrong type, control characters) | `202`; valid recipients are generated; invalid ones are stored as `FAILED` with the reason and listed in `rejected_recipients` |
| Duplicate email in one request (case-insensitive) | First occurrence is kept; later ones are rejected like invalid recipients |

Names are trimmed and inner whitespace is collapsed. Limits: name 100 chars, event/course 120 chars, email 254 chars.

Strictness rules worth knowing:

- **Email** must be a plain address (`name@example.com`). Display-name forms such as `John <john@example.com>`, or values containing spaces, commas or semicolons, are rejected instead of being silently rewritten.
- **`certificate_date`** must be a string in `YYYY-MM-DD` format, with a year between 1900 and 2100. Numbers (timestamps) and date-times are rejected. The same person can receive certificates in different jobs; duplicates are only detected inside one request.

## Design decisions

1. **Why FastAPI?** Request/response validation with Pydantic, automatic OpenAPI docs, built-in `BackgroundTasks`, dependency injection, and a good test client, with very little boilerplate.
2. **Why SQLite?** The assignment asks for a relational database that is easy to run. SQLite needs no server, and SQLAlchemy keeps the code portable (see "Moving to PostgreSQL"). WAL mode is enabled so polling reads are not blocked while the background task writes.
3. **Why SQLAlchemy?** A proper ORM with relationships and foreign keys, parameterized queries (no SQL injection), and a one-line switch of database.
4. **Why ReportLab?** Pure-Python PDF generation with precise layout control and no external binaries (unlike HTML-to-PDF tools that need a browser or wkhtmltopdf).
5. **Why BackgroundTasks instead of Celery/Redis?** The assignment explicitly allows simple background processing. `BackgroundTasks` is built into FastAPI, needs no extra infrastructure and still gives the important property: the client gets an immediate `202` and polls for progress. See limitations below for what this trade-off costs.
6. **Why synchronous generation inside the background task?** ReportLab is CPU-bound, blocking code, so `async` would add nothing. `process_job` is a plain `def`, which Starlette runs in a worker thread, so it does not block the event loop and other requests (including status polling) stay responsive. Certificates are generated one at a time on purpose: it keeps progress counters and failure handling simple and predictable.
7. **How failures are isolated:** one `try/except` per certificate, failure reason stored on the row, loop continues. See [Failure handling](#failure-handling).
8. **How progress is calculated:** `(successful + failed) / total * 100`; counters are updated and committed after each certificate in the same transaction as the certificate's status.
9. **How files are stored:** `storage/certificates/<job_id>/<certificate_id>.pdf`; the database keeps only the *relative* path. See below.
10. **How certificate IDs are generated:** primary keys are UUIDv4 strings. The human-facing certificate number is `CERT-` + 12 random hex characters (48 bits), with a `UNIQUE` database constraint as the final guarantee.

Other decisions: an app factory for testability; a per-recipient `position` column so listings keep the order of the request; a ZIP built in memory because job sizes are bounded by `MAX_RECIPIENTS_PER_JOB`.

## Database design

```
generation_jobs                          certificates
-----------------                        ----------------------
id (PK, uuid)            1 ───────< *    id (PK, uuid)
event_name                               job_id (FK -> generation_jobs.id, ON DELETE CASCADE, indexed)
course_name                              position            (index in the request; UNIQUE with job_id)
certificate_date                         recipient_name
status                                   recipient_email
total_recipients                         certificate_number  (UNIQUE, indexed)
successful_count                         file_path           (relative to storage dir, NULL unless generated)
failed_count                             status              (PENDING | GENERATED | FAILED)
created_at                               error_message       (NULL unless failed)
started_at                               created_at
completed_at
```

- Foreign keys are enforced (`PRAGMA foreign_keys=ON`).
- Timestamps are timezone-aware UTC (a small `TypeDecorator` restores the UTC tzinfo that SQLite drops).
- Counters on the job are a deliberate denormalization: status polling reads one row instead of counting certificates.

## Certificate storage strategy

- PDFs live under `storage/certificates/<job_id>/<certificate_id>.pdf`. Filenames are generated by the server from UUIDs, never from client input, so there is nothing to inject.
- The database stores the path **relative** to the storage directory. Before any file is read or written the path is resolved and checked to be inside the storage directory (`resolve_within`); anything else is refused. The API never returns filesystem paths.
- Names shown to users (download filename, ZIP entries) pass through `sanitize_filename` (ASCII only, no separators, length-limited) and include the certificate number so they are unique.
- `storage/certificates/*.pdf` and the job sub-directories are git-ignored; only `.gitkeep` is tracked.

## Error handling

- Domain errors (`NotFoundError` → 404, `ConflictError` → 409, `BadRequestError` → 400) are raised by services and turned into `{"detail": "..."}` by one handler.
- FastAPI's own validation errors are returned as `422` in its standard format (a `detail` list).
- Any unexpected exception returns `500 {"detail": "Internal server error"}`; the traceback is only logged.
- Per-recipient failures are stored in `certificates.error_message` and surfaced by the listing endpoint.

## Limitations and future improvements

Being honest about what this is: a well-structured single-process service, **not** a distributed system.

- **Server crash during generation:** `BackgroundTasks` lives inside the server process. If the process dies, the job stays `PROCESSING`. Certificates already generated are kept. Recovery would be a startup step that re-queues `PENDING` certificates (the design already makes processing resumable because it only picks `PENDING` rows).
- **Multiple workers:** with `uvicorn --workers N` each job is still processed by exactly one worker (the one that received the request), but there is no cross-process queue or retry.
- **Throughput:** certificates are generated sequentially. A real queue (Celery/RQ/Arq) with a worker pool would parallelize and survive restarts.
- **SQLite** allows one writer at a time. Fine here; use PostgreSQL for concurrent writers.
- **Fonts:** Latin script only. Supporting other scripts means embedding a Unicode TTF font.
- **ZIP in memory:** fine up to the 1000-recipient limit; larger jobs should stream the archive. If a generated PDF has been removed from disk, the ZIP skips it (and logs a warning) instead of failing; if no file is available the response is 404.
- **Certificate listing is not paginated** (bounded by the 1000-recipient limit).
- **No delete endpoint:** deleting a job directly in the database cascades to its certificate rows but leaves the PDF files on disk, so a cleanup/retention job would be needed in production.
- **No auth, rate limiting, retention/cleanup of old files, or schema migrations** (Alembic would be the next step).

Moving to PostgreSQL: change `DATABASE_URL` (e.g. `postgresql+psycopg://...`) and install the driver; the code already applies the SQLite-specific options only for SQLite URLs. Add Alembic for migrations.

## Example curl commands

```bash
# Create a job (5 recipients)
curl -s -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Backend Workshop",
    "course_name": "Advanced Python",
    "certificate_date": "2026-10-07",
    "recipients": [
      {"name": "John Doe", "email": "john@example.com"},
      {"name": "Jane Smith", "email": "jane@example.com"},
      {"name": "Alex Johnson", "email": "alex@example.com"},
      {"name": "Priya Sharma", "email": "priya@example.com"},
      {"name": "Rahul Kumar", "email": "rahul@example.com"}
    ]
  }'

JOB_ID=<job_id from the response>

# Poll status and progress
curl -s http://127.0.0.1:8000/api/v1/jobs/$JOB_ID

# List the certificates of the job
curl -s http://127.0.0.1:8000/api/v1/jobs/$JOB_ID/certificates

# Download one PDF (use a certificate_id from the listing)
curl -s -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>

# Download all generated PDFs as a ZIP
curl -s -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/$JOB_ID/certificates/download

# Invalid input: empty recipient list -> 422
curl -s -X POST http://127.0.0.1:8000/api/v1/jobs -H "Content-Type: application/json" \
  -d '{"event_name":"E","course_name":"C","certificate_date":"2026-10-07","recipients":[]}'

# Unknown job -> 404
curl -s http://127.0.0.1:8000/api/v1/jobs/does-not-exist
```

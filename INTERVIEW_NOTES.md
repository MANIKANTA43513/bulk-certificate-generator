# Interview Notes: Bulk Certificate Generator

Use this as a speaking guide. Everything here matches the actual code; if you are asked about something, you can open the file mentioned and show it.

## 1. The 60-second explanation

> "It's a FastAPI backend for generating certificates in bulk. A client sends **one** request with the event details and a list of recipients. The API validates the request, saves a job and one certificate row per recipient in SQLite, and immediately returns a `job_id` with `202 Accepted`. The actual PDF generation runs in a FastAPI background task using ReportLab. Each certificate is generated independently, so if one fails, the reason is saved and the rest continue. The client polls the job endpoint to see total, successful and failed counts and a progress percentage, then downloads individual PDFs or a ZIP. I wrote 143 pytest tests, and I'm upfront that BackgroundTasks is a simple, single-process choice rather than a production-scale queue."

## 2. The 2-minute explanation

Start with the 60-second version, then add:

> "The code is layered: routes only handle HTTP, services hold the business logic, and the database and PDF code are separate. Validation works on two levels. Request-level problems, like a missing event name or an empty recipient list, return 422. Recipient-level problems, like a bad email, don't reject the whole job: I validate each recipient independently, so valid recipients still get certificates, and invalid ones are stored as FAILED with a reason and also listed in the response. If *every* recipient is invalid I return 400 and create nothing.
>
> The job moves PENDING → PROCESSING → COMPLETED, COMPLETED_WITH_ERRORS or FAILED. After each certificate I commit the updated counters, so polling shows real progress: progress is processed divided by total. I found a real bug while testing live: the last counter commit and the final status commit were separate, so a client could briefly see 100% on an unfinished job. I fixed it by setting the final status in the same commit as the last certificate and added a regression test.
>
> Files are stored by UUID under `storage/certificates/<job_id>/`, the database only holds relative paths, and every path is checked to stay inside the storage directory. Limitations I know about: if the server crashes mid-job the job stays PROCESSING, and for large scale I'd move to a real queue with PostgreSQL."

## 3. Architecture

- **Routes** (`app/api/routes`): parse input, call a service, shape the response. No logic.
- **Services** (`app/services`): `job_service` (validation, create job, background processing) and `certificate_service` (generate one certificate, find files, build ZIP).
- **DB** (`app/db`): SQLAlchemy engine/session and two models.
- **Utils** (`app/utils`): the ReportLab template and the path/filename safety helpers.
- **`create_app(settings)`** is a factory. Each app instance gets its own engine and settings, so every test uses a temporary database and storage folder.
- Errors: services raise `NotFoundError`/`ConflictError`/`BadRequestError`; one handler converts them to `{"detail": ...}`.

## 4. Database

Two tables, one-to-many:

- `generation_jobs`: id, event, course, date, status, `total_recipients`, `successful_count`, `failed_count`, `created_at`, `started_at`, `completed_at`.
- `certificates`: id, `job_id` (FK, cascade delete, indexed), `position` (order in the request), name, email, `certificate_number` (UNIQUE), `file_path` (relative), status (PENDING/GENERATED/FAILED), `error_message`, `created_at`.

The counters on the job are intentional denormalization so a status poll reads a single row. SQLite runs with foreign keys on and WAL mode, so polling can read while the background task writes.

## 5. API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/jobs` | create job, 202 |
| `GET /api/v1/jobs/{id}` | status + progress |
| `GET /api/v1/jobs/{id}/certificates` | per-recipient results + download links |
| `GET /api/v1/jobs/{id}/certificates/download` | ZIP of successful PDFs |
| `GET /api/v1/certificates/{id}` | one PDF |

Status codes: 202 accepted, 400 business validation, 404 not found, 409 conflicting state (certificate failed or not ready; ZIP requested while nothing is ready yet), 422 schema validation, 500 unexpected.

## 6. BackgroundTasks

FastAPI runs a function **after the response is sent**. In `create_job` I call `background_tasks.add_task(process_job, job.id, session_factory, settings)`. Important details:

- The task gets its **own database session**, because the request's session is closed by then.
- It receives only the `job_id`, not ORM objects, and reloads the job itself.
- It only starts if the job is `PENDING`, so it can never run twice for one job.
- `process_job` is a normal `def`, so Starlette runs it in a thread pool and the event loop stays free for polling requests.
- Limitation: it lives inside the server process. No retries, no survival across restarts.

## 7. Certificate generation

ReportLab canvas, landscape A4, one template: border, organization, title, recipient name, course, event, date, certificate number, signature line. Long text shrinks to fit. The file is written to a `.tmp` file and renamed, so no half-written PDFs. The built-in fonts only cover Western-European characters and draw black boxes for others, so I check the text first and fail that certificate with a clear message. That is also a genuine, natural failure case that demonstrates isolation.

## 8. Failure isolation

`certificate_service.generate_certificate` wraps **one** recipient in its own `try`:

1. `CertificateGenerationError` (expected): store the specific message.
2. `OSError`: store "Could not write the certificate file"; details only in logs.
3. Any other exception: log the traceback, store a generic message (no internals leaked to the client).

It never raises, so the loop continues. Counters are committed after each certificate. The final job status is `COMPLETED` (no failures), `COMPLETED_WITH_ERRORS` (some of each), or `FAILED` (zero successes). A separate outer handler only covers job-level problems like a DB failure.

## 9. Testing

143 tests in 6 files, each with a temporary DB and storage directory. Highlights:

- Failure isolation: I patch the PDF renderer to raise for one recipient and assert the others are still generated, and that the job ends `COMPLETED_WITH_ERRORS` with 5 successes and 1 failure.
- **Live progress**: a spy renderer reads the job status from a second session each time a certificate starts, and asserts `0%, 20%, 40%, 60%, 80%`.
- A commit listener test proves no committed state shows 100% while still `PROCESSING`.
- PDFs are opened with `pypdf` and the name, course, date and certificate number are verified; ZIP contents are verified.
- Security: tampered `file_path` in the DB is never served; path traversal is rejected; generation refuses to write outside the storage directory even if a job ID were tampered with.
- Database integrity: foreign keys, unique constraints, NOT NULL and cascade deletes are tested directly.
- Concurrency: one test polls the real API while another thread processes a 120-recipient job and checks every response for impossible states.
- I also deliberately broke the code (mutation checks) to confirm the tests fail when isolation, status logic or the path guard is removed.

## 10. Trade-offs

| Choice | Benefit | Cost |
| --- | --- | --- |
| BackgroundTasks | zero infrastructure, simple | not durable, single process, no retries |
| SQLite | zero setup | one writer at a time |
| Sequential generation | simple, predictable progress | slower than a worker pool |
| Partial processing of bad recipients | useful for real data | response must clearly report rejects |
| ZIP built in memory | simple | not for huge jobs |
| Built-in PDF fonts | no font files | Latin characters only |

## 11. Likely questions and strong, concise answers

**0. (Quick answers to the "why" questions)** FastAPI: validation + docs + background tasks with little code. SQLite: zero-setup relational DB. SQLAlchemy: ORM, relationships, parameterized queries, portable to PostgreSQL. ReportLab: pure-Python PDFs with precise layout and no external binaries. BackgroundTasks: no infrastructure but still a 202 + polling design.

**1. Why FastAPI?**
Pydantic validation and automatic Swagger docs come for free, it has built-in `BackgroundTasks` and dependency injection, and it is quick to write and test.

**2. Why SQLite?**
The task needs a relational database that anyone can run without installing a server. SQLAlchemy keeps the code portable, so moving to PostgreSQL is mostly a connection-string change.

**2b. Why SQLAlchemy and not raw SQL?**
It gives me models and relationships (job → certificates), automatic parameter binding so there is no SQL injection risk, and it lets me change database by changing the URL. For two tables it is also less code than hand-written SQL.

**2c. Why ReportLab?**
It is a pure-Python library with exact control over the page layout, and it needs no browser or external binary, unlike HTML-to-PDF tools. The cost is that built-in fonts only cover Western-European characters.

**3. Why BackgroundTasks?**
The brief allows simple background processing. It gives the key behaviour, an immediate `202` and polling for progress, with no extra infrastructure. I'm honest that it isn't durable.

**4. Why not Celery?**
Celery needs a broker like Redis and separate worker processes. That is more moving parts than this assignment warrants. It's the right next step if jobs must survive restarts or run in parallel.

**5. How does bulk processing work?**
One `POST` carries the whole recipient list. I store one job and N certificate rows in a single transaction, return the job ID, and a background task walks through the pending certificates one by one.

**6. What happens if one certificate fails?**
Its status becomes `FAILED`, the reason goes into `error_message`, the job's `failed_count` increases, and the loop continues. At the end the job is `COMPLETED_WITH_ERRORS`.

**7. How do you track progress?**
After each certificate I update and commit the job's successful/failed counters. Progress is `(successful + failed) / total * 100`. Clients poll `GET /jobs/{id}`.

**8. How do you prevent duplicate certificates?**
Two levels. Within a request, a second recipient with the same email (case-insensitive) is rejected with a reason. At the database level, `certificate_number` has a UNIQUE constraint, and `(job_id, position)` is unique. Also a job can only start from `PENDING`, so it can't be processed twice. I don't block the same person across *different* jobs, because that can be legitimate.

**9. How are PDFs generated?**
With ReportLab's canvas API from one fixed template. The recipient, course, event, date, organization and certificate number are drawn onto it. The file is written to a temp name and renamed.

**10. How are files stored?**
At `storage/certificates/<job_id>/<certificate_id>.pdf`. File names come from UUIDs, never user input. The database stores only the relative path, and every read/write path is checked to be inside the storage directory.

**11. How do you prevent path traversal?**
Server-generated UUID filenames, plus `resolve_within`, which resolves the path and rejects anything outside the storage directory. Download names are passed through `sanitize_filename`. There's a test that tampers with a DB path and verifies it's never served.

**12. How would you scale this system?**
Introduce a real queue (Celery/RQ with Redis), run several workers, move to PostgreSQL, and store files in object storage (S3). Workers would claim certificates using `SELECT ... FOR UPDATE SKIP LOCKED` so two workers never take the same row.

**13. What happens if the server crashes during generation?**
The job stays `PROCESSING` and the remaining certificates stay `PENDING`; already generated PDFs are kept. Because processing only picks `PENDING` rows, a startup recovery step could resume them. I haven't built that; I list it as a limitation.

**14. How would you move from SQLite to PostgreSQL?**
Change `DATABASE_URL`, install a driver such as psycopg, and add Alembic for migrations instead of `create_all`. The SQLite-only settings (WAL, `check_same_thread`) are already applied only for SQLite URLs.

**15. How would you handle 100,000 recipients?**
Not in one request. Accept the upload as a file or in batches, enqueue the work, process with multiple workers, update progress in batches instead of one commit per row, paginate the certificate listing, stream the ZIP instead of building it in memory, and keep files in object storage.

**16. How would you make generation truly asynchronous?**
Move the work out of the web process into a task queue. The API only enqueues and returns the job ID; separate worker processes consume tasks, can retry failures, and survive restarts. The API contract (create job, poll status, download) stays identical.

**17. Why is the generation code synchronous inside the background task?**
ReportLab is CPU-bound blocking code, so `async` wouldn't speed it up. Using a plain `def` makes Starlette run it in a thread pool, so the event loop isn't blocked and status polling stays responsive.

**18. Why does the background task open its own database session?**
The request-scoped session is closed when the response finishes, and SQLAlchemy sessions aren't thread-safe. The task creates and owns a fresh session.

**19. How do you handle invalid recipients?**
Per-recipient validation. Valid ones are generated; invalid ones are stored as `FAILED` with a reason and listed in the response. If none are valid, 400. Request-level problems like an empty list are 422.

**19b. What does the email/date validation do beyond Pydantic's defaults?**
A final audit showed two gaps. `EmailStr` accepts `John <john@example.com>` and silently rewrites it, so I reject display-name forms. And a `date` field accepts numbers like `0` (1970-01-01) and date-times, so I require `YYYY-MM-DD` strings with a year between 1900 and 2100. Both have tests that fail if I remove the checks.

**19c. What if a generated PDF is deleted from disk?**
Single download returns 404 "Certificate file is no longer available". The ZIP skips the missing file and logs a warning rather than failing the whole download; if nothing is available it returns 404.

**20. What bug did you find while testing, and how did you fix it?**
During a live run, a status poll once showed `PROCESSING` with 100% progress and no completion time. The last counter commit and the final-status commit were separate. I now set the final status and `completed_at` in the same commit as the last certificate, and a test listens to every commit to prove no inconsistent state is ever stored.

**21. Why return 409 for a failed certificate download?**
The resource exists but is in a state that conflicts with the request (no PDF). 404 would suggest the ID is wrong, and 500 would suggest a server bug. The body explains the failure reason.

**22. How do you avoid leaking internals?**
A global handler returns `{"detail": "Internal server error"}` for unexpected errors and logs the traceback server-side. Failure messages stored for unexpected errors are generic, and API responses never contain filesystem paths.

**23. What would you improve first?**
Crash recovery for stuck jobs, then a real queue with retries, then Unicode fonts, authentication, and file retention/cleanup.

## 12. Future production architecture

If this had to run at real scale, I would change it step by step without changing the public API:

1. **PostgreSQL** instead of SQLite, with Alembic migrations.
2. **A task queue** (Celery/RQ/Arq with Redis). The API only inserts the job and enqueues it; worker processes generate certificates in parallel, claim rows with `SELECT ... FOR UPDATE SKIP LOCKED`, and retry failures.
3. **Object storage** (e.g. S3) for PDFs, with short-lived signed download URLs, instead of local disk.
4. **Crash recovery**: workers resume `PENDING` certificates; a watchdog marks jobs stuck in `PROCESSING` and re-queues them.
5. **Batch progress updates** (e.g. every 50 certificates) and **pagination** of the certificate listing; streamed ZIPs.
6. **Authentication**, per-client rate limits, request size limits, retention/cleanup of old files, metrics and structured logging.
7. **Unicode fonts** embedded in the template for non-Latin names.

## 13. Things to say honestly if asked

- "This is single-process background processing, not a distributed system."
- "If the server dies mid-job, the job needs manual or startup recovery; I haven't implemented it."
- "Fonts are Latin-only today; other scripts fail with a clear message instead of producing a broken PDF."
- "There's no authentication, rate limiting or migration tool, because they were out of scope."

# Task Queue Platform

A Django REST Framework task queue API backed by Celery, Redis, and PostgreSQL. Clients submit supported jobs, then retrieve their lifecycle and result asynchronously. The API supports JWT authentication, idempotent creation, ownership isolation, task retry/cancellation, and best-effort webhook delivery that is independent of job execution.

## Live deployment

- **API:** https://web-production-0d58f.up.railway.app
- **Application health:** https://web-production-0d58f.up.railway.app/health/
- **Readiness:** https://web-production-0d58f.up.railway.app/ready/
- **API docs:** https://web-production-0d58f.up.railway.app/api/docs/

## Architecture

- Django 5.1 and Django REST Framework
- Celery + Redis for asynchronous work and results
- PostgreSQL in production; SQLite is available for local fallback/testing
- JWT for new clients; existing DRF token authentication remains accepted for compatibility
- Gunicorn + WhiteNoise for web serving

## Task status lifecycle

`pending` → `running` → `success`

A transient execution error moves a job to `retrying`; the task becomes `failed` only after Celery exhausts its configured retries. A queued/running task may be moved to `canceled`. Webhook delivery runs separately, so a webhook failure never changes a successful task into a failed one.

## Quick start

```bash
cp .env.example .env
# Set a real SECRET_KEY and choose config.settings.dev for local development.
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DJANGO_SETTINGS_MODULE=config.settings.dev python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.dev python manage.py runserver
```

For Docker-based development, run `docker-compose up --build`, then migrate with `docker-compose exec web python manage.py migrate`.

Start a worker in another shell:

```bash
DJANGO_SETTINGS_MODULE=config.settings.dev celery -A config worker --loglevel=info
```

## Authentication

Register, then exchange credentials for access and refresh tokens:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"a-strong-password"}'

curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"a-strong-password"}'
```

Pass the access token as `Authorization: Bearer <access-token>`. The legacy `Authorization: Token <token>` mechanism remains supported during migration.

## API

All task endpoints are under `/api/v1/` and only return tasks owned by the authenticated user.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/auth/register/` | Register a user |
| POST | `/auth/login/` | Obtain JWT access and refresh tokens |
| POST | `/auth/refresh/` | Refresh an access token |
| POST | `/tasks/` | Create a task |
| GET | `/tasks/` | List the caller's tasks (paginated) |
| GET | `/tasks/{id}/` | Retrieve one task |
| POST | `/tasks/{id}/retry/` | Retry a failed or canceled task |
| POST | `/tasks/{id}/cancel/` | Cancel a pending/running task |

Create an idempotent task:

```bash
curl -X POST http://localhost:8000/api/v1/tasks/ \
  -H 'Authorization: Bearer <access-token>' \
  -H 'Idempotency-Key: invoice-123' \
  -H 'Content-Type: application/json' \
  -d '{"task_type":"echo","payload":{"hello":"world"}}'
```

Submitting the same idempotency key again for the same user returns the original task rather than creating another one. The key is unique per owner.

Supported types are `echo` and `send_email`. `send_email` requires `to`, `subject`, and `message` in its payload and uses Django's configured email backend.

## Webhooks

Pass an optional `webhook_url` at creation. Terminal task outcomes are posted with task ID, type, status, result, and error. Delivery uses a short configurable timeout and exponential retry; webhook status, attempts, and the latest delivery error are recorded on the task. Execution status is never changed by a delivery failure.

## Health and documentation

- `/health/` is a lightweight liveness endpoint.
- `/ready/` checks the database before returning `200`.
- `/api/schema/` publishes OpenAPI JSON.
- `/api/docs/` serves interactive Swagger UI.

## Environment variables

See `.env.example`. Production requires `SECRET_KEY` and a non-empty `ALLOWED_HOSTS`. Important variables include `DATABASE_URL`, `REDIS_URL`, `CORS_ALLOWED_ORIGINS`, `DEFAULT_FROM_EMAIL`, `TASK_WEBHOOK_TIMEOUT`, and `SECURE_SSL_REDIRECT`.

## Railway deployment notes

Configure PostgreSQL and Redis, then set `DJANGO_SETTINGS_MODULE=config.settings.prod`, a long random `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, and `ALLOWED_HOSTS`.

- Railway wildcard hosts use Django's leading-dot syntax: `.up.railway.app`, **not** `*.up.railway.app`.
- Railway terminates TLS at its edge. Set `SECURE_SSL_REDIRECT=False` so its internal plain-HTTP health check receives a `200`, not a redirect.
- Run web and Celery worker services with the same application configuration. The `Procfile` includes web, worker, and migration release commands.

## Testing and CI

Run:

```bash
DJANGO_SETTINGS_MODULE=config.settings.dev SECRET_KEY=test-secret python manage.py check
DJANGO_SETTINGS_MODULE=config.settings.dev SECRET_KEY=test-secret python manage.py test
```

GitHub Actions runs checks, migration consistency validation, and the test suite for pushes and pull requests.

## Deployment

Production deployment configured through Railway.

# Task Queue Platform

A production-ready Django + DRF + Celery + Redis job queue API. Submit a task,
it runs asynchronously via a Celery worker, poll (or get webhooked) for the result.

## Stack

- Django 5.1 + Django REST Framework
- Celery 5 + Redis (broker + result backend)
- Postgres (production), SQLite (local fallback)
- Token authentication
- Gunicorn + WhiteNoise for serving

## API

All endpoints are under `/api/v1/`.

### Get an auth token
```
POST /api/v1/auth/token/
{ "username": "you", "password": "..." }
```

### Create a task
```
POST /api/v1/tasks/
Authorization: Token <your-token>

{
  "task_type": "echo",
  "payload": { "hello": "world" },
  "webhook_url": "https://yourapp.com/webhooks/task-complete"  // optional
}
```

### Check status
```
GET /api/v1/tasks/<id>/
```

### List your tasks
```
GET /api/v1/tasks/
```

Built-in task types: `echo` (test), `send_email` (requires `to`, `subject`, `message`
in payload and email backend configured). Add more handlers in `tasks/executors.py`
inside the `dispatch()` function.

## Local development

```bash
cp .env.example .env   # edit values, generate a fresh SECRET_KEY
docker-compose up --build
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

API at http://localhost:8000/api/v1/, admin at http://localhost:8000/admin/.

## Deploy to Railway

1. Push this project to a GitHub repo.
2. On Railway: **New Project → Deploy from GitHub repo**, pick this repo.
3. Add two plugins: **PostgreSQL** and **Redis**. Railway auto-injects
   `DATABASE_URL`. Copy the Redis connection string into a `REDIS_URL` variable.
4. In the web service's Variables tab, set:
   - `SECRET_KEY` — generate a fresh one, don't reuse the example
   - `DJANGO_SETTINGS_MODULE=config.settings.prod`
   - `ALLOWED_HOSTS=<your-app>.up.railway.app`
   - `REDIS_URL` — from step 3
   - `CORS_ALLOWED_ORIGINS` — your frontend origin, if any
5. Railway builds from the `Dockerfile` automatically and runs `release:` (migrate)
   from the Procfile before each deploy.
6. Add a second service from the same repo for the worker. Set its start command to:
   ```
   celery -A config worker --loglevel=info
   ```
   Give it the same env vars as the web service.
7. Once deployed, run once via Railway's shell or CLI:
   ```
   railway run python manage.py createsuperuser
   ```
8. Confirm health: `GET https://<your-app>.up.railway.app/healthz/`

## Notes

- Webhook delivery is best-effort (5s timeout, no retry queue). For guaranteed
  delivery, add a `WebhookDelivery` model and retry via Celery.
- `send_email` uses Django's email backend; set `EMAIL_BACKEND` and SMTP vars
  in settings/prod.py for real delivery, or leave console backend for testing.
- Rate limiting defaults to 100 requests/minute per user via DRF throttling.

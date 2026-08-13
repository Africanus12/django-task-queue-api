FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic imports production settings. Generate an ephemeral, build-only
# Fernet key so production's fail-closed credential-key guard is exercised
# without baking a deployment secret into the image.
RUN AI_CREDENTIAL_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    DJANGO_SETTINGS_MODULE=config.settings.prod SECRET_KEY=build-only \
    DATABASE_URL=sqlite:///build.sqlite3 REDIS_URL=redis://localhost:6379/0 \
    python manage.py collectstatic --noinput

CMD gunicorn config.wsgi:application --bind 0.0.0.0:$PORT

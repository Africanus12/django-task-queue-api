from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env(DEBUG=(bool, False))
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-secret-key-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "rest_framework", "rest_framework.authtoken", "corsheaders", "tasks", "drf_spectacular", "drf_spectacular_sidecar"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "config.security.SecurityHeadersMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "corsheaders.middleware.CorsMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.debug", "django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///" + str(BASE_DIR / "db.sqlite3"))}
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}, {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}, {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}, {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE, TIME_ZONE, USE_I18N, USE_TZ = "en-us", "UTC", True, True
STATIC_URL, STATIC_ROOT = "static/", BASE_DIR / "staticfiles"
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication", "rest_framework.authentication.TokenAuthentication"], "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"], "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination", "PAGE_SIZE": 20, "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"], "DEFAULT_THROTTLE_RATES": {"user": "100/minute", "ai_generate": env("AI_GENERATE_RATE", default="5/minute"), "ai_model_discovery": env("AI_MODEL_DISCOVERY_RATE", default="10/minute"), "ai_credential": env("AI_CREDENTIAL_RATE", default="5/minute")}, "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema"}
# Bound request buffering before parsers deserialize JSON. Endpoint serializers impose
# tighter field-level limits for API keys, models, and prompts.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MEMORY_SIZE", default=131072)
DATA_UPLOAD_MAX_NUMBER_FIELDS = env.int("DATA_UPLOAD_MAX_NUMBER_FIELDS", default=100)
SPECTACULAR_SETTINGS = {"TITLE": "Task Queue API", "VERSION": "1.0.0", "SWAGGER_UI_DIST": "SIDECAR", "SWAGGER_UI_FAVICON_HREF": "SIDECAR"}
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_TRACK_STARTED, CELERY_TASK_TIME_LIMIT = True, 300
CELERY_BEAT_SCHEDULE = {"cleanup-expired-ai-credentials": {"task": "tasks.executors.cleanup_expired_credentials", "schedule": 300}}
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
TASK_WEBHOOK_TIMEOUT = env.int("TASK_WEBHOOK_TIMEOUT", default=5)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.invalid")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
POKEE_API_KEY = env("POKEE_API_KEY", default="")
AI_CREDENTIAL_TTL_SECONDS = env.int("AI_CREDENTIAL_TTL_SECONDS", default=300)
AI_CREDENTIAL_ENCRYPTION_KEY = env("AI_CREDENTIAL_ENCRYPTION_KEY", default="")
AI_MAX_OUTPUT_TOKENS = env.int("AI_MAX_OUTPUT_TOKENS", default=8192)
AI_MAX_PROMPT_CHARS = env.int("AI_MAX_PROMPT_CHARS", default=20000)

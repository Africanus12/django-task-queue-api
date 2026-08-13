from .base import *  # noqa

DEBUG = False

if SECRET_KEY == "dev-insecure-secret-key-change-me":
    raise RuntimeError("SECRET_KEY must be set in production.")
if not ALLOWED_HOSTS:
    raise RuntimeError("ALLOWED_HOSTS must be configured in production.")
if not AI_CREDENTIAL_ENCRYPTION_KEY:
    raise RuntimeError("AI_CREDENTIAL_ENCRYPTION_KEY must be set in production.")

# Fail closed at startup instead of discovering a malformed credential key while
# handling a request (which could otherwise produce an operational 500).
from cryptography.fernet import Fernet
try:
    Fernet(AI_CREDENTIAL_ENCRYPTION_KEY.encode())
except (TypeError, ValueError) as exc:
    raise RuntimeError("AI_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key.") from exc

# Railway terminates TLS at its edge. Set this to False there so internal HTTP
# health checks do not receive redirects.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = "same-origin"
SECURE_PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
SECURE_CONTENT_SECURITY_POLICY = "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:"

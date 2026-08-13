import hashlib
import hmac

from django.conf import settings


def openai_safety_identifier(user_id):
    """Stable opaque OpenAI safety identifier; no user PII leaves this service."""
    digest = hmac.new(settings.SECRET_KEY.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()
    return f"usr_{digest}"


def safe_provider_error(exc):
    """Never return arbitrary upstream exception text to clients or task records."""
    if str(exc) in {"AI credential expired; submit a new task.", "AI credential expired or is unavailable; submit a new task."}:
        return str(exc)
    return "AI provider request failed."

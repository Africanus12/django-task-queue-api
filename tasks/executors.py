import logging

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import Task, TaskCredential
from .providers import get_provider
from .security import openai_safety_identifier, safe_provider_error

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_credentials():
    """Safe maintenance task; no credential values are read or logged."""
    return TaskCredential.objects.filter(expires_at__lte=timezone.now()).delete()[0]


@shared_task(bind=True, max_retries=3)
def process_task(self, task_id):
    try:
        with transaction.atomic():
            task = Task.objects.select_for_update().filter(id=task_id).first()
            if task is None or task.status not in {Task.Status.PENDING, Task.Status.RETRYING}:
                return None
            task.status = Task.Status.RUNNING
            task.save(update_fields=["status", "updated_at"])
        result = dispatch(task.task_type, task.payload, task=task)
    except Exception as exc:
        retry_task = False
        with transaction.atomic():
            task = Task.objects.select_for_update().filter(id=task_id).first()
            if task is None:
                return None
            task.error = safe_provider_error(exc) if task.task_type == "ai_generate" else str(exc)
            task.retries = self.request.retries + 1
            retry_task = self.request.retries < self.max_retries
            task.status = Task.Status.RETRYING if retry_task else Task.Status.FAILED
            task.save(update_fields=["status", "error", "retries", "updated_at"])
        # Raise outside atomic() so the retrying status is committed before Celery
        # schedules the next execution.
        if retry_task:
            raise self.retry(exc=exc, countdown=min(2 ** task.retries, 300))
        if task.task_type == "ai_generate":
            TaskCredential.objects.filter(task=task).delete()
        deliver_webhook.delay(str(task.id))
        return None

    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=task_id)
        if task.status == Task.Status.CANCELED:
            return None
        task.status, task.result, task.error = Task.Status.SUCCESS, result, None
        task.save(update_fields=["status", "result", "error", "updated_at"])
        if task.task_type == "ai_generate":
            TaskCredential.objects.filter(task=task).delete()
    deliver_webhook.delay(str(task.id))
    return result


def dispatch(task_type, payload, task=None):
    if task_type == "ai_generate":
        provider = get_provider(payload["provider"])
        api_key = getattr(settings, {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "isaac": "POKEE_API_KEY"}[payload["provider"]], "")
        credential = None
        if task is not None:
            credential = TaskCredential.objects.filter(task=task).first()
        if credential and credential.expires_at > timezone.now():
            api_key = credential.decrypt()
        if not api_key:
            raise ValueError("AI credential expired; submit a new task.")
        options = {key: payload[key] for key in ("temperature", "max_tokens") if key in payload}
        if task.owner_id and payload["provider"] == "openai":
            options["safety_identifier"] = openai_safety_identifier(task.owner_id)
        return provider.generate(api_key=api_key, prompt=payload["prompt"], model=payload.get("model"), **options)
    if task_type == "echo":
        return {"echoed": payload}
    if task_type == "send_email":
        required = {"to", "subject", "message"}
        missing = required - payload.keys()
        if missing:
            raise ValueError(f"Missing fields for send_email: {sorted(missing)}")
        send_mail(payload["subject"], payload["message"], settings.DEFAULT_FROM_EMAIL, [payload["to"]], fail_silently=False)
        return {"sent_to": payload["to"]}
    raise ValueError(f"Unknown task_type: {task_type}")


@shared_task(bind=True, max_retries=3)
def deliver_webhook(self, task_id):
    task = Task.objects.filter(id=task_id).first()
    if not task or not task.webhook_url:
        return None
    try:
        response = requests.post(task.webhook_url, json={"id": str(task.id), "task_type": task.task_type, "status": task.status, "result": task.result, "error": task.error}, timeout=settings.TASK_WEBHOOK_TIMEOUT)
        response.raise_for_status()
        Task.objects.filter(id=task_id).update(webhook_status=Task.WebhookStatus.DELIVERED, webhook_attempts=self.request.retries + 1, webhook_error=None)
    except requests.RequestException as exc:
        attempts = self.request.retries + 1
        Task.objects.filter(id=task_id).update(webhook_status=Task.WebhookStatus.FAILED, webhook_attempts=attempts, webhook_error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(2 ** attempts, 300))
        logger.warning("Webhook delivery permanently failed for task %s", task_id)

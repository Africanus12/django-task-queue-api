import logging

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .models import Task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_task(self, task_id):
    task = Task.objects.get(id=task_id)
    task.status = "running"
    task.save(update_fields=["status"])

    try:
        result = dispatch(task.task_type, task.payload)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        task.retries += 1
        task.save(update_fields=["status", "error", "retries"])
        _fire_webhook(task)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return None

    task.status = "success"
    task.result = result
    task.save(update_fields=["status", "result"])
    _fire_webhook(task)
    return result


def dispatch(task_type, payload):
    handlers = {
        "echo": _handle_echo,
        "send_email": _handle_send_email,
    }
    handler = handlers.get(task_type)
    if not handler:
        raise ValueError(f"Unknown task_type: {task_type}")
    return handler(payload)


def _handle_echo(payload):
    return {"echoed": payload}


def _handle_send_email(payload):
    required = {"to", "subject", "message"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"Missing fields for send_email: {missing}")

    send_mail(
        subject=payload["subject"],
        message=payload["message"],
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[payload["to"]],
        fail_silently=False,
    )
    return {"sent_to": payload["to"]}


def _fire_webhook(task):
    if not task.webhook_url:
        return
    try:
        requests.post(
            task.webhook_url,
            json={
                "id": str(task.id),
                "task_type": task.task_type,
                "status": task.status,
                "result": task.result,
                "error": task.error,
            },
            timeout=getattr(settings, "TASK_WEBHOOK_TIMEOUT", 5),
        )
    except requests.RequestException:
        logger.warning("Webhook delivery failed for task %s", task.id)

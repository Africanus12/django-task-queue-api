import uuid

from django.conf import settings
from django.db import models


class Task(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        RETRYING = "retrying", "Retrying"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    class WebhookStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks")
    task_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    retries = models.PositiveSmallIntegerField(default=0)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    webhook_url = models.URLField(null=True, blank=True)
    webhook_status = models.CharField(max_length=20, choices=WebhookStatus.choices, default=WebhookStatus.PENDING)
    webhook_attempts = models.PositiveSmallIntegerField(default=0)
    webhook_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["owner", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_task_idempotency_key_per_owner",
            )
        ]

    def __str__(self):
        return f"{self.task_type} ({self.status})"

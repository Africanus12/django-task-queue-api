from rest_framework import serializers
from .models import Task


class TaskCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ["id", "task_type", "payload", "webhook_url", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class TaskDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id", "task_type", "payload", "status", "result",
            "error", "retries", "webhook_url", "created_at", "updated_at",
        ]
        read_only_fields = fields

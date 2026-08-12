from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Task


class TaskCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ["id", "task_type", "payload", "webhook_url", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def validate_task_type(self, value):
        if value not in {"echo", "send_email"}:
            raise serializers.ValidationError("Unsupported task_type.")
        return value


class TaskDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id", "task_type", "payload", "status", "result", "error", "retries",
            "webhook_url", "webhook_status", "webhook_attempts", "webhook_error",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})

    class Meta:
        model = get_user_model()
        fields = ["username", "password", "email"]
        extra_kwargs = {"email": {"required": False}}

    def create(self, validated_data):
        return get_user_model().objects.create_user(**validated_data)

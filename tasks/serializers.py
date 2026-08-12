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
            raise serializers.ValidationError("Unsupported task_type. Use the dedicated AI endpoint for ai_generate.")
        return value


class AIGenerateSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["openai", "gemini", "isaac"])
    api_key = serializers.CharField(write_only=True, trim_whitespace=True, max_length=512)
    model = serializers.CharField(required=False, allow_blank=False, max_length=200)
    prompt = serializers.CharField(trim_whitespace=True, max_length=100_000)
    temperature = serializers.FloatField(required=False, min_value=0, max_value=2)
    max_tokens = serializers.IntegerField(required=False, min_value=1, max_value=60_000)

    def validate_api_key(self, value):
        if not value:
            raise serializers.ValidationError("An API key is required.")
        return value

    def validate_prompt(self, value):
        if not value:
            raise serializers.ValidationError("A prompt is required.")
        return value


class EmptySerializer(serializers.Serializer):
    pass


class APIKeySerializer(serializers.Serializer):
    api_key = serializers.CharField(write_only=True, trim_whitespace=True, max_length=512)

    def validate_api_key(self, value):
        if not value:
            raise serializers.ValidationError("An API key is required.")
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

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import render
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .executors import process_task
from .models import Task, TaskCredential
from .providers import ProviderError, get_provider, provider_metadata
from .serializers import AIGenerateSerializer, APIKeySerializer, EmptySerializer, RegistrationSerializer, TaskCreateSerializer, TaskDetailSerializer
from .throttles import AIGenerateThrottle, AIModelDiscoveryThrottle


class RegistrationView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [permissions.AllowAny]


class TaskListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        return TaskCreateSerializer if self.request.method == "POST" else TaskDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key and len(idempotency_key) > 255:
            return Response({"detail": "Idempotency-Key must be at most 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                task, created = Task.objects.get_or_create(owner=request.user, idempotency_key=idempotency_key, defaults={**serializer.validated_data, "idempotency_key": idempotency_key}) if idempotency_key else (Task.objects.create(owner=request.user, **serializer.validated_data), True)
                if created:
                    transaction.on_commit(lambda: process_task.delay(str(task.id)))
        except IntegrityError:
            task, created = Task.objects.get_or_create(owner=request.user, idempotency_key=idempotency_key)
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class TaskDetailView(generics.RetrieveAPIView):
    serializer_class = TaskDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)


def _server_key(provider):
    return getattr(settings, {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "isaac": "POKEE_API_KEY"}.get(provider, ""), "")


class TaskRetryView(APIView):
    serializer_class = TaskDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        with transaction.atomic():
            task = Task.objects.select_for_update().filter(id=id, owner=request.user).first()
            if not task:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if task.status not in {Task.Status.FAILED, Task.Status.CANCELED}:
                return Response({"detail": "Only failed or canceled tasks can be retried."}, status=status.HTTP_409_CONFLICT)
            if task.task_type == "ai_generate" and not hasattr(task, "credential") and not _server_key(task.payload.get("provider")):
                return Response({"detail": "AI credential expired; submit a new task."}, status=status.HTTP_409_CONFLICT)
            task.status, task.error, task.result, task.retries = Task.Status.PENDING, None, None, 0
            task.save(update_fields=["status", "error", "result", "retries", "updated_at"])
            transaction.on_commit(lambda: process_task.delay(str(task.id)))
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_202_ACCEPTED)


class TaskCancelView(APIView):
    serializer_class = TaskDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        with transaction.atomic():
            task = Task.objects.select_for_update().filter(id=id, owner=request.user).first()
            if not task:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if task.status in {Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELED}:
                return Response({"detail": "Task cannot be canceled in its current state."}, status=status.HTTP_409_CONFLICT)
            task.status = Task.Status.CANCELED
            task.save(update_fields=["status", "updated_at"])
            if task.task_type == "ai_generate":
                TaskCredential.objects.filter(task=task).delete()
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_202_ACCEPTED)


class AIProviderListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EmptySerializer
    def get(self, request):
        return Response({"providers": provider_metadata()})


class AIModelListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIModelDiscoveryThrottle]
    serializer_class = APIKeySerializer
    def post(self, request, provider):
        if provider not in {"openai", "gemini", "isaac"}:
            return Response({"detail": "Unsupported AI provider."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = APIKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            models = get_provider(provider).list_models(serializer.validated_data["api_key"])
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"provider": provider, "models": models})


class AIGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIGenerateThrottle]
    serializer_class = AIGenerateSerializer
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data, idempotency_key = serializer.validated_data, request.headers.get("Idempotency-Key")
        if idempotency_key and len(idempotency_key) > 255:
            return Response({"detail": "Idempotency-Key must be at most 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
        payload = {key: data[key] for key in ("provider", "model", "prompt", "temperature", "max_tokens") if key in data}
        try:
            with transaction.atomic():
                task, created = Task.objects.get_or_create(owner=request.user, idempotency_key=idempotency_key, defaults={"task_type": "ai_generate", "payload": payload, "idempotency_key": idempotency_key}) if idempotency_key else (Task.objects.create(owner=request.user, task_type="ai_generate", payload=payload), True)
                if created:
                    TaskCredential.objects.create(task=task, encrypted_api_key=TaskCredential.encrypt(data["api_key"]), expires_at=timezone.now() + timedelta(seconds=settings.AI_CREDENTIAL_TTL_SECONDS))
                    transaction.on_commit(lambda: process_task.delay(str(task.id)))
        except IntegrityError:
            task, created = Task.objects.get_or_create(owner=request.user, idempotency_key=idempotency_key)
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PlaygroundView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = EmptySerializer
    def get(self, request):
        return render(request, "tasks/playground.html")

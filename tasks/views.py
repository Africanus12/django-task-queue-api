from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import render
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .executors import process_task
from .models import ProviderCredential, Task, TaskCredential
from .providers import ProviderError, get_provider, provider_metadata
from .serializers import AIGenerateSerializer, APIKeySerializer, EmptySerializer, ProviderCredentialSerializer, RegistrationSerializer, StoredCredentialGenerateSerializer, TaskCreateSerializer, TaskDetailSerializer
from .throttles import AICredentialThrottle, AIGenerateThrottle, AIModelDiscoveryThrottle


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
            has_saved_credential = task.payload.get("credential_source") == "saved" and ProviderCredential.objects.filter(owner=request.user, provider=task.payload.get("provider")).exists()
            if task.task_type == "ai_generate" and not hasattr(task, "credential") and not has_saved_credential and not _server_key(task.payload.get("provider")):
                return Response({"detail": "AI credential expired or is unavailable; submit a new task."}, status=status.HTTP_409_CONFLICT)
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


def _validate_idempotency_key(request):
    key = request.headers.get("Idempotency-Key")
    if key and len(key) > 255:
        return None, Response({"detail": "Idempotency-Key must be at most 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
    return key, None


def _queue_ai_task(owner, data, idempotency_key, api_key=None, credential_source=None):
    payload = {key: data[key] for key in ("provider", "model", "prompt", "temperature", "max_tokens") if key in data}
    if credential_source:
        payload["credential_source"] = credential_source
    try:
        with transaction.atomic():
            task, created = Task.objects.get_or_create(owner=owner, idempotency_key=idempotency_key, defaults={"task_type": "ai_generate", "payload": payload, "idempotency_key": idempotency_key}) if idempotency_key else (Task.objects.create(owner=owner, task_type="ai_generate", payload=payload), True)
            if created and api_key:
                TaskCredential.objects.create(task=task, encrypted_api_key=TaskCredential.encrypt(api_key), expires_at=timezone.now() + timedelta(seconds=settings.AI_CREDENTIAL_TTL_SECONDS))
            if created:
                transaction.on_commit(lambda: process_task.delay(str(task.id)))
    except IntegrityError:
        task, created = Task.objects.get_or_create(owner=owner, idempotency_key=idempotency_key)
    return task, created


class ProviderCredentialView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProviderCredentialSerializer

    def get_throttles(self):
        # Reads/deletes do not invoke provider APIs. Only writes trigger external
        # validation and need a tighter anti-abuse limit.
        return [AICredentialThrottle()] if self.request.method == "PUT" else []

    def get(self, request, provider):
        if provider not in {"openai", "gemini", "isaac"}:
            return Response({"detail": "Unsupported AI provider."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"provider": provider, "configured": ProviderCredential.objects.filter(owner=request.user, provider=provider).exists()})

    def put(self, request, provider):
        if provider not in {"openai", "gemini", "isaac"}:
            return Response({"detail": "Unsupported AI provider."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.serializer_class(data={**request.data, "provider": provider})
        serializer.is_valid(raise_exception=True)
        try:
            get_provider(provider).validate_api_key(serializer.validated_data["api_key"])
        except ProviderError:
            return Response({"detail": "Unable to validate this API key."}, status=status.HTTP_400_BAD_REQUEST)
        ProviderCredential.objects.update_or_create(
            owner=request.user,
            provider=provider,
            defaults={"encrypted_api_key": ProviderCredential.encrypt(serializer.validated_data["api_key"])},
        )
        return Response({"provider": provider, "configured": True})

    def delete(self, request, provider):
        if provider not in {"openai", "gemini", "isaac"}:
            return Response({"detail": "Unsupported AI provider."}, status=status.HTTP_400_BAD_REQUEST)
        ProviderCredential.objects.filter(owner=request.user, provider=provider).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoredCredentialGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIGenerateThrottle]
    serializer_class = StoredCredentialGenerateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key, error_response = _validate_idempotency_key(request)
        if error_response:
            return error_response
        credential = ProviderCredential.objects.filter(owner=request.user, provider=serializer.validated_data["provider"]).first()
        if not credential:
            return Response({"detail": "No saved credential for this provider."}, status=status.HTTP_409_CONFLICT)
        task, created = _queue_ai_task(request.user, serializer.validated_data, idempotency_key, credential_source="saved")
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AIGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AIGenerateThrottle]
    serializer_class = AIGenerateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key, error_response = _validate_idempotency_key(request)
        if error_response:
            return error_response
        task, created = _queue_ai_task(request.user, serializer.validated_data, idempotency_key, api_key=serializer.validated_data["api_key"])
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PlaygroundView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = EmptySerializer
    def get(self, request):
        return render(request, "tasks/playground.html")

from django.db import IntegrityError, transaction
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .executors import process_task
from .models import Task
from .serializers import RegistrationSerializer, TaskCreateSerializer, TaskDetailSerializer


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
                task, created = Task.objects.get_or_create(
                    owner=request.user,
                    idempotency_key=idempotency_key,
                    defaults={**serializer.validated_data, "idempotency_key": idempotency_key},
                ) if idempotency_key else (Task.objects.create(owner=request.user, **serializer.validated_data), True)
                if created:
                    transaction.on_commit(lambda: process_task.delay(str(task.id)))
        except IntegrityError:
            task, created = Task.objects.get_or_create(owner=request.user, idempotency_key=idempotency_key)
        output = TaskDetailSerializer(task).data
        return Response(output, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class TaskDetailView(generics.RetrieveAPIView):
    serializer_class = TaskDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)


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
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_202_ACCEPTED)

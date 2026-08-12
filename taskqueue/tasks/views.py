from rest_framework import generics, permissions

from .executors import process_task
from .models import Task
from .serializers import TaskCreateSerializer, TaskDetailSerializer


class TaskListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        return TaskCreateSerializer

    def perform_create(self, serializer):
        task = serializer.save(owner=self.request.user)
        process_task.delay(str(task.id))


class TaskDetailView(generics.RetrieveAPIView):
    serializer_class = TaskDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

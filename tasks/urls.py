from django.urls import path

from .views import TaskDetailView, TaskListCreateView

urlpatterns = [
    path("tasks/", TaskListCreateView.as_view(), name="task-list-create"),
    path("tasks/<uuid:id>/", TaskDetailView.as_view(), name="task-detail"),
]

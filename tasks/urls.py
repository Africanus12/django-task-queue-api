from django.urls import path

from .views import RegistrationView, TaskCancelView, TaskDetailView, TaskListCreateView, TaskRetryView

urlpatterns = [
    path("auth/register/", RegistrationView.as_view(), name="register"),
    path("tasks/", TaskListCreateView.as_view(), name="task-list-create"),
    path("tasks/<uuid:id>/", TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<uuid:id>/retry/", TaskRetryView.as_view(), name="task-retry"),
    path("tasks/<uuid:id>/cancel/", TaskCancelView.as_view(), name="task-cancel"),
]

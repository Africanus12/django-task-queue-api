from django.urls import path

from .views import AIGenerateView, AIModelListView, AIProviderListView, PlaygroundView, ProviderCredentialView, RegistrationView, StoredCredentialGenerateView, TaskCancelView, TaskDetailView, TaskListCreateView, TaskRetryView

urlpatterns = [
    path("playground/", PlaygroundView.as_view(), name="playground"),
    path("auth/register/", RegistrationView.as_view(), name="register"),
    path("tasks/", TaskListCreateView.as_view(), name="task-list-create"),
    path("tasks/<uuid:id>/", TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<uuid:id>/retry/", TaskRetryView.as_view(), name="task-retry"),
    path("tasks/<uuid:id>/cancel/", TaskCancelView.as_view(), name="task-cancel"),
    path("ai/providers/", AIProviderListView.as_view(), name="ai-provider-list"),
    path("ai/providers/<str:provider>/models/", AIModelListView.as_view(), name="ai-model-list"),
    path("ai/generate/", AIGenerateView.as_view(), name="ai-generate"),
    path("ai/credentials/<str:provider>/", ProviderCredentialView.as_view(), name="ai-provider-credential"),
    path("ai/generate/saved/", StoredCredentialGenerateView.as_view(), name="ai-generate-saved"),
]

from django.contrib import admin
from django.urls import path, include
from rest_framework.authtoken.views import obtain_auth_token
from django.http import JsonResponse

def health(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health, name="health"),
    path("api/v1/", include("tasks.urls")),
    path("api/v1/auth/token/", obtain_auth_token, name="api-token"),
]

from django.contrib import admin
from django.db import connections
from django.http import JsonResponse
from django.conf import settings
import redis
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


def health(request):
    return JsonResponse({"status": "ok"})


def ready(request):
    try:
        connections["default"].cursor().execute("SELECT 1")
        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health, name="health"),
    path("health/", health),
    path("ready/", ready),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerSplitView.as_view(url_name="schema"), name="docs"),
    path("api/v1/auth/login/", TokenObtainPairView.as_view(), name="token-obtain-pair"),
    path("api/v1/auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("api/v1/", include("tasks.urls")),
]

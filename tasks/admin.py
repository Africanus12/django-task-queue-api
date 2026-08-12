from django.contrib import admin
from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "task_type", "owner", "status", "retries", "created_at")
    list_filter = ("status", "task_type")
    search_fields = ("id", "task_type", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")

from django.contrib import admin
from .models import Notification

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "title",
        "notification_type",
        "priority",
        "is_read",
        "created_at",
    ]
    list_filter = ["notification_type", "priority", "is_read", "created_at"]
    search_fields = ["title", "message", "reference_id"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]

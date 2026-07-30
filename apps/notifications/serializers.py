from rest_framework import serializers
from .models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    created_at_formatted = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "notification_type",
            "priority",
            "reference_type",
            "reference_id",
            "action_url",
            "icon",
            "is_read",
            "created_at",
            "created_at_formatted",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_created_at_formatted(self, obj):
        # Return a simple ISO format or standard string representation
        return obj.created_at.isoformat()

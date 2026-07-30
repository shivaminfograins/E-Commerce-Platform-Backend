from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.generics import ListAPIView, DestroyAPIView
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from apps.admin_panel.permissions import AdminPermission
from .models import Notification
from .serializers import NotificationSerializer
from .services import NotificationService

class NotificationPagination(PageNumberPagination):
    page_size = 15
    page_size_query_param = "page_size"
    max_page_size = 100

class NotificationListView(ListAPIView):
    """
    GET /api/admin/notifications/
    Returns paginated notifications. Supports searching, sorting, and filtering.
    """
    serializer_class = NotificationSerializer
    permission_classes = [AdminPermission]
    pagination_class = NotificationPagination

    def get_queryset(self):
        # Base optimized queryset
        queryset = Notification.objects.all()

        # Filtering by read state
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            if is_read.lower() == "true":
                queryset = queryset.filter(is_read=True)
            elif is_read.lower() == "false":
                queryset = queryset.filter(is_read=False)

        # Filtering by type
        notification_type = self.request.query_params.get("notification_type")
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type.upper())

        # Filtering by priority
        priority = self.request.query_params.get("priority")
        if priority:
            queryset = queryset.filter(priority=priority.upper())

        # Search query (matches title or message)
        search_query = self.request.query_params.get("search")
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) | Q(message__icontains=search_query)
            )

        # Ordering
        ordering = self.request.query_params.get("ordering", "-created_at")
        allowed_orderings = ["created_at", "-created_at", "priority", "-priority", "notification_type", "-notification_type"]
        if ordering in allowed_orderings:
            queryset = queryset.order_by(ordering)
        else:
            queryset = queryset.order_by("-created_at")

        # Limit for dropdown if requested
        limit = self.request.query_params.get("limit")
        if limit:
            try:
                queryset = queryset[:int(limit)]
            except ValueError:
                pass

        return queryset

class UnreadCountView(APIView):
    """
    GET /api/admin/notifications/unread-count/
    Returns the count of unread notifications.
    """
    permission_classes = [AdminPermission]

    def get(self, request):
        count = Notification.objects.filter(is_read=False).count()
        return Response({"count": count}, status=status.HTTP_200_OK)

class MarkReadView(APIView):
    """
    PATCH /api/admin/notifications/{id}/mark-read/
    Marks a single notification as read.
    """
    permission_classes = [AdminPermission]

    def patch(self, request, pk):
        try:
            notification = Notification.objects.get(pk=pk)
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
            return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)

class MarkAllReadView(APIView):
    """
    PATCH /api/admin/notifications/mark-all-read/
    Marks all notifications as read.
    """
    permission_classes = [AdminPermission]

    def patch(self, request):
        Notification.objects.filter(is_read=False).update(is_read=True)
        return Response({"message": "All notifications marked as read"}, status=status.HTTP_200_OK)

class NotificationDeleteView(DestroyAPIView):
    """
    DELETE /api/admin/notifications/{id}/
    Deletes a notification.
    """
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [AdminPermission]

class TestNotificationView(APIView):
    """
    POST /api/admin/notifications/test/
    Creates a mock/test notification.
    """
    permission_classes = [AdminPermission]

    def post(self, request):
        notification_type = request.data.get("notification_type", "SYSTEM")
        priority = request.data.get("priority", "MEDIUM")
        title = request.data.get("title", f"Test {notification_type} Alert")
        message = request.data.get("message", "This is a test notification generated for development purposes.")
        
        notification = NotificationService.create(
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            reference_type=request.data.get("reference_type"),
            reference_id=request.data.get("reference_id"),
            action_url=request.data.get("action_url"),
            icon=request.data.get("icon")
        )
        if notification:
            return Response(NotificationSerializer(notification).data, status=status.HTTP_201_CREATED)
        return Response({"error": "Failed to create notification"}, status=status.HTTP_400_BAD_REQUEST)

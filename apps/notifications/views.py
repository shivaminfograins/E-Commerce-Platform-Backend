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

from rest_framework.permissions import IsAuthenticated

class CustomerNotificationListView(ListAPIView):
    """
    GET /api/notifications/
    Returns paginated notifications belonging exclusively to request.user.
    """
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user)

        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            if is_read.lower() == "true":
                queryset = queryset.filter(is_read=True)
            elif is_read.lower() == "false":
                queryset = queryset.filter(is_read=False)

        notification_type = self.request.query_params.get("notification_type")
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type.upper())

        return queryset.order_by("-created_at")

class CustomerUnreadCountView(APIView):
    """
    GET /api/notifications/unread-count/
    Returns unread count for request.user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({"unread_count": count, "count": count}, status=status.HTTP_200_OK)

class CustomerMarkReadView(APIView):
    """
    PATCH /api/notifications/<pk>/read/
    Marks a single notification belonging to request.user as read.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        try:
            notification = Notification.objects.get(pk=pk, user=request.user)
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
            return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)

class CustomerMarkAllReadView(APIView):
    """
    POST /api/notifications/read-all/ or PATCH /api/notifications/mark-all-read/
    Marks all notifications belonging to request.user as read.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return self._mark_all(request)

    def patch(self, request):
        return self._mark_all(request)

    def _mark_all(self, request):
        updated_count = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"message": "All notifications marked as read", "updated_count": updated_count}, status=status.HTTP_200_OK)

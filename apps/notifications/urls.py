from django.urls import path
from .views import (
    NotificationListView,
    UnreadCountView,
    MarkReadView,
    MarkAllReadView,
    NotificationDeleteView,
    CustomerNotificationListView,
    CustomerUnreadCountView,
    CustomerMarkReadView,
    CustomerMarkAllReadView,
)

urlpatterns = [
    # Customer endpoints (/api/notifications/...)
    path("", CustomerNotificationListView.as_view(), name="customer-notification-list"),
    path("unread-count/", CustomerUnreadCountView.as_view(), name="customer-unread-count"),
    path("read-all/", CustomerMarkAllReadView.as_view(), name="customer-read-all"),
    path("mark-all-read/", CustomerMarkAllReadView.as_view(), name="customer-mark-all-read"),
    path("<int:pk>/read/", CustomerMarkReadView.as_view(), name="customer-mark-read"),
    path("<int:pk>/mark-read/", CustomerMarkReadView.as_view(), name="customer-mark-read-alt"),

    # Legacy / Admin endpoints (/api/admin/notifications/...)
    path("admin-list/", NotificationListView.as_view(), name="admin-notification-list"),
    path("admin-unread-count/", UnreadCountView.as_view(), name="admin-unread-count"),
]

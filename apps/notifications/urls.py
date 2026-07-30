from django.urls import path
from .views import (
    NotificationListView,
    UnreadCountView,
    MarkReadView,
    MarkAllReadView,
    NotificationDeleteView,
    TestNotificationView,
)

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path("unread-count/", UnreadCountView.as_view(), name="unread-count"),
    path("mark-all-read/", MarkAllReadView.as_view(), name="mark-all-read"),
    path("<int:pk>/mark-read/", MarkReadView.as_view(), name="mark-read"),
    path("<int:pk>/", NotificationDeleteView.as_view(), name="notification-delete"),
    path("test/", TestNotificationView.as_view(), name="test-notification"),
]

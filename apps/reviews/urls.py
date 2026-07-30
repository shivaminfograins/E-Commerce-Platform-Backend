from django.urls import path
from .views import (
    ReviewCreateUpdateDeleteView,
    ReviewHelpfulToggleView,
    ReviewReportView,
    MyReviewsListView,
    AdminReviewListView,
    AdminReviewModerateView,
    AdminReviewBulkModerateView,
    AdminReviewAnalyticsView,
)

app_name = "reviews"

urlpatterns = [
    # ── Review actions ──
    path("", ReviewCreateUpdateDeleteView.as_view(), name="review-create"),
    path("<int:pk>/", ReviewCreateUpdateDeleteView.as_view(), name="review-update-delete"),
    path("<int:pk>/helpful/", ReviewHelpfulToggleView.as_view(), name="review-helpful"),
    path("<int:pk>/report/", ReviewReportView.as_view(), name="review-report"),
    
    # User reviews
    path("my-reviews/", MyReviewsListView.as_view(), name="my-reviews"),

    # ── Admin Moderation & Analytics ──
    path("admin/list/", AdminReviewListView.as_view(), name="admin-review-list"),
    path("admin/<int:pk>/moderate/", AdminReviewModerateView.as_view(), name="admin-review-moderate"),
    path("admin/bulk-moderate/", AdminReviewBulkModerateView.as_view(), name="admin-review-bulk-moderate"),
    path("admin/analytics/", AdminReviewAnalyticsView.as_view(), name="admin-review-analytics"),
]

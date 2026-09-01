from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AdminSettlementViewSet,
    VendorSettlementListView,
    VendorSettlementDetailView,
    VendorSettlementSummaryView,
)

admin_router = DefaultRouter()
admin_router.register("admin/settlements", AdminSettlementViewSet, basename="admin-settlements")

urlpatterns = [
    # Vendor endpoints
    path("vendor/settlements/", VendorSettlementListView.as_view(), name="vendor-settlement-list"),
    path("vendor/settlements/summary/", VendorSettlementSummaryView.as_view(), name="vendor-settlement-summary"),
    path("vendor/settlements/<int:pk>/", VendorSettlementDetailView.as_view(), name="vendor-settlement-detail"),

    # Admin ViewSet routes
    path("", include(admin_router.urls)),
]

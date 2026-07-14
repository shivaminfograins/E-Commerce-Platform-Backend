from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AdminLoginView,
    AdminDashboardView,
    AdminCategoryViewSet,
    AdminProductViewSet,
    AdminProductVariantViewSet,
    AdminProductImageViewSet,
    AdminCustomerViewSet,
    AdminAddressViewSet,
    AdminOrderViewSet,
    AdminReportsViewSet,
    AdminProfileView,
    AdminChangePasswordView,
    AdminSettingsView,
)

router = DefaultRouter()
router.register("categories", AdminCategoryViewSet, basename="admin-categories")
router.register("products", AdminProductViewSet, basename="admin-products")
router.register("customers", AdminCustomerViewSet, basename="admin-customers")
router.register("addresses", AdminAddressViewSet, basename="admin-addresses")
router.register("orders", AdminOrderViewSet, basename="admin-orders")

urlpatterns = [
    path("auth/login/", AdminLoginView.as_view(), name="admin_login"),
    path("dashboard/", AdminDashboardView.as_view(), name="admin_dashboard"),
    
    # Reports
    path("reports/sales/", AdminReportsViewSet.as_view({"get": "sales"}), name="admin-reports-sales"),
    path("reports/revenue/", AdminReportsViewSet.as_view({"get": "revenue"}), name="admin-reports-revenue"),
    path("reports/orders/", AdminReportsViewSet.as_view({"get": "orders"}), name="admin-reports-orders"),
    path("reports/customers/", AdminReportsViewSet.as_view({"get": "customers"}), name="admin-reports-customers"),
    path("reports/products/", AdminReportsViewSet.as_view({"get": "products"}), name="admin-reports-products"),

    # Profile & settings
    path("profile/", AdminProfileView.as_view(), name="admin-profile"),
    path("change-password/", AdminChangePasswordView.as_view(), name="admin-change-password"),
    path("settings/", AdminSettingsView.as_view(), name="admin-settings"),
    
    # Nested and Flat routes for Variants
    path("products/<int:product_id>/variants/", AdminProductVariantViewSet.as_view({"get": "list", "post": "create"}), name="admin-product-variants"),
    path("variants/<int:pk>/", AdminProductVariantViewSet.as_view({"put": "update", "patch": "partial_update", "delete": "destroy"}), name="admin-variants-detail"),
    
    # Nested and Flat routes for Images
    path("products/<int:product_id>/images/", AdminProductImageViewSet.as_view({"post": "create"}), name="admin-product-images"),
    path("images/<int:pk>/", AdminProductImageViewSet.as_view({"delete": "destroy"}), name="admin-images-detail"),

    path("", include(router.urls)),
]



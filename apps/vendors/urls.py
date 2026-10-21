from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    VendorApplyView,
    VendorProfileView,
    VendorResubmitView,
    VendorDocumentDownloadView,
    VendorDashboardView,
    VendorCatalogProductListView,
    VendorCatalogProductDetailView,
    VendorProductViewSet,
    VendorProductVariantDeleteView,
    VendorProductVariantImageCreateView,
    VendorProductVariantImageDeleteView,
    VendorProductRequestViewSet,
    VendorInventoryListView,
    VendorInventoryDetailView,
    VendorInventoryHistoryListView,
    VendorProductInventoryMovementView,
    VendorVariantInventoryMovementView,
)
from apps.orders.views import (
    VendorOrderListView,
    VendorOrderDetailView,
    VendorOrderStatusUpdateView,
    VendorOrderShipView,
    VendorOrderCancelView,
)

from apps.settlements.views import (
    VendorSettlementListView,
    VendorSettlementDetailView,
    VendorSettlementSummaryView,
)

from apps.returns.views import VendorReturnViewSet
from apps.coupons.views import VendorCouponViewSet

vendor_return_router = DefaultRouter()
vendor_return_router.register(r"", VendorReturnViewSet, basename="vendor-returns")

vendor_coupon_router = DefaultRouter()
vendor_coupon_router.register(r"", VendorCouponViewSet, basename="vendor-coupons")

urlpatterns = [
    path("apply/", VendorApplyView.as_view(), name="vendor-apply"),
    path("profile/", VendorProfileView.as_view(), name="vendor-profile"),
    path("profile/resubmit/", VendorResubmitView.as_view(), name="vendor-resubmit"),
    path("documents/<int:pk>/download/", VendorDocumentDownloadView.as_view(), name="vendor-document-download"),
    
    # Dashboard
    path("dashboard/", VendorDashboardView.as_view(), name="vendor-dashboard"),
    
    # Master catalog reference
    path("catalog/products/", VendorCatalogProductListView.as_view(), name="vendor-catalog-product-list"),
    path("catalog/products/<int:pk>/", VendorCatalogProductDetailView.as_view(), name="vendor-catalog-product-detail"),
    
    # Vendor listings CRUD
    path("products/", VendorProductViewSet.as_view({"get": "list", "post": "create"}), name="vendor-product-list"),
    path("product-requests/", VendorProductRequestViewSet.as_view({"get": "list", "post": "create"}), name="vendor-product-request-list"),
    path("product-requests/<int:pk>/", VendorProductRequestViewSet.as_view({"get": "retrieve", "delete": "destroy"}), name="vendor-product-request-detail"),
    path("products/<int:pk>/", VendorProductViewSet.as_view({"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}), name="vendor-product-detail"),
    
    # Explicit variant deletion
    path("products/variants/<int:pk>/", VendorProductVariantDeleteView.as_view(), name="vendor-product-variant-delete"),
    path("products/variants/<int:variant_id>/images/", VendorProductVariantImageCreateView.as_view(), name="vendor-variant-image-create"),
    path("products/variants/images/<int:pk>/", VendorProductVariantImageDeleteView.as_view(), name="vendor-variant-image-delete"),

    # Inventory and Movements
    path("inventory/movements/", VendorInventoryHistoryListView.as_view(), name="vendor-inventory-history"),
    path("inventory/", VendorInventoryListView.as_view(), name="vendor-inventory-list"),
    path("inventory/<int:pk>/", VendorInventoryDetailView.as_view(), name="vendor-inventory-detail"),
    path("inventory/<int:product_id>/movements/", VendorProductInventoryMovementView.as_view(), name="vendor-product-movement"),
    path("inventory/<int:product_id>/variants/<int:variant_id>/movements/", VendorVariantInventoryMovementView.as_view(), name="vendor-variant-movement"),

    # Order management
    path("orders/", VendorOrderListView.as_view(), name="vendor-order-list"),
    path("orders/<str:vendor_order_number>/", VendorOrderDetailView.as_view(), name="vendor-order-detail"),
    path("orders/<str:vendor_order_number>/status/", VendorOrderStatusUpdateView.as_view(), name="vendor-order-status-update"),
    path("orders/<str:vendor_order_number>/ship/", VendorOrderShipView.as_view(), name="vendor-order-ship"),
    path("orders/<str:vendor_order_number>/cancel/", VendorOrderCancelView.as_view(), name="vendor-order-cancel"),

    # Settlement management
    path("settlements/", VendorSettlementListView.as_view(), name="vendor-settlement-list"),
    path("settlements/summary/", VendorSettlementSummaryView.as_view(), name="vendor-settlement-summary"),
    path("settlements/<int:pk>/", VendorSettlementDetailView.as_view(), name="vendor-settlement-detail"),

    # Coupon management
    path("coupons/", include(vendor_coupon_router.urls)),

    # Return management
    path("returns/", include(vendor_return_router.urls)),
]

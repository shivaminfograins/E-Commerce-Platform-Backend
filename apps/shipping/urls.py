from django.urls import path
from apps.shipping.views import (
    VendorOrderCreateShipmentView,
    VendorOrderRequestPickupView,
    VendorOrderGenerateLabelView,
    VendorOrderGenerateManifestView,
    VendorOrderTrackingView,
    ShiprocketWebhookView,
)

urlpatterns = [
    path(
        "vendor-orders/<str:vendor_order_number>/create-shipment/",
        VendorOrderCreateShipmentView.as_view(),
        name="vendor-order-create-shipment",
    ),
    path(
        "vendor-orders/<str:vendor_order_number>/request-pickup/",
        VendorOrderRequestPickupView.as_view(),
        name="vendor-order-request-pickup",
    ),
    path(
        "vendor-orders/<str:vendor_order_number>/generate-label/",
        VendorOrderGenerateLabelView.as_view(),
        name="vendor-order-generate-label",
    ),
    path(
        "vendor-orders/<str:vendor_order_number>/generate-manifest/",
        VendorOrderGenerateManifestView.as_view(),
        name="vendor-order-generate-manifest",
    ),
    path(
        "vendor-orders/<str:vendor_order_number>/tracking/",
        VendorOrderTrackingView.as_view(),
        name="vendor-order-tracking",
    ),
    path(
        "webhooks/shiprocket/",
        ShiprocketWebhookView.as_view(),
        name="shiprocket-webhook",
    ),
]

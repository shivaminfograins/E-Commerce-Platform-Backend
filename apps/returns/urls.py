from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CustomerReturnViewSet, VendorReturnViewSet, AdminReturnViewSet

router = DefaultRouter()
router.register(r"", CustomerReturnViewSet, basename="customer-returns")

vendor_router = DefaultRouter()
vendor_router.register(r"", VendorReturnViewSet, basename="vendor-returns")

admin_router = DefaultRouter()
admin_router.register(r"", AdminReturnViewSet, basename="admin-returns")

urlpatterns = [
    path("vendor/", include(vendor_router.urls)),
    path("admin/", include(admin_router.urls)),
    path("", include(router.urls)),
]

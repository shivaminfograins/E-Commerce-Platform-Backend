import hmac
import logging
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import VendorOrder
from apps.shipping.models import Shipment
from apps.shipping.exceptions import ShippingValidationError, ShippingConcurrencyError, ShippingProviderError
from apps.shipping.services import ShippingService
from apps.shipping.serializers import ShipmentSerializer, ShipmentStatusHistorySerializer

logger = logging.getLogger(__name__)


class VendorOrderCreateShipmentView(APIView):
    """
    POST /api/shipping/vendor-orders/<vendor_order_number>/create-shipment/
    Creates external shipment (e.g. Shiprocket) and assigns AWB for a VendorOrder.
    Must be an approved vendor (owning the order) or admin user.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, vendor_order_number):
        user = request.user
        is_admin = getattr(user, "is_staff", False) or getattr(user, "role", "") == "ADMIN"
        is_vendor = hasattr(user, "vendor_profile") and user.vendor_profile.status == "APPROVED"

        if not (is_admin or is_vendor):
            return Response(
                {"detail": "You do not have permission to perform this action. Approved vendor or admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_admin:
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number)
        else:
            vendor = user.vendor_profile
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number, vendor=vendor)

        courier_id = request.data.get("courier_id")
        provider_name = request.data.get("provider_name", "shiprocket")

        try:
            shipment, is_new = ShippingService.create_shipment(
                vendor_order=vendor_order,
                courier_id=courier_id,
                provider_name=provider_name,
            )
        except ShippingConcurrencyError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except ShippingValidationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ShippingProviderError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ShipmentSerializer(shipment)
        res_status = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
        return Response(
            {
                "success": True,
                "message": "Shipment created successfully." if is_new else "Existing shipment returned.",
                "shipment": serializer.data,
            },
            status=res_status,
        )


class VendorOrderRequestPickupView(APIView):
    """
    POST /api/shipping/vendor-orders/<vendor_order_number>/request-pickup/
    Schedules courier pickup for an existing shipment.
    Must be an approved vendor (owning the order) or admin user.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, vendor_order_number):
        user = request.user
        is_admin = getattr(user, "is_staff", False) or getattr(user, "role", "") == "ADMIN"
        is_vendor = hasattr(user, "vendor_profile") and user.vendor_profile.status == "APPROVED"

        if not (is_admin or is_vendor):
            return Response(
                {"detail": "Approved vendor or admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_admin:
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number)
        else:
            vendor = user.vendor_profile
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number, vendor=vendor)

        if not hasattr(vendor_order, "shipment"):
            return Response(
                {"detail": "No shipment record exists for this vendor order. Create a shipment first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shipment = vendor_order.shipment

        try:
            shipment, is_requested = ShippingService.request_pickup(shipment)
        except ShippingConcurrencyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ShippingValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ShippingProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        serializer = ShipmentSerializer(shipment)
        return Response(
            {
                "success": True,
                "message": "Pickup scheduled successfully." if is_requested else "Pickup already scheduled.",
                "shipment": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class VendorOrderGenerateLabelView(APIView):
    """
    POST /api/shipping/vendor-orders/<vendor_order_number>/generate-label/
    Generates or retrieves shipping label PDF URL for a shipment.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, vendor_order_number):
        user = request.user
        is_admin = getattr(user, "is_staff", False) or getattr(user, "role", "") == "ADMIN"
        is_vendor = hasattr(user, "vendor_profile") and user.vendor_profile.status == "APPROVED"

        if not (is_admin or is_vendor):
            return Response(
                {"detail": "Approved vendor or admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_admin:
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number)
        else:
            vendor = user.vendor_profile
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number, vendor=vendor)

        if not hasattr(vendor_order, "shipment"):
            return Response(
                {"detail": "No shipment record exists for this vendor order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shipment = vendor_order.shipment

        try:
            label_url = ShippingService.generate_label(shipment)
        except ShippingConcurrencyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ShippingValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ShippingProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "success": True,
                "label_url": label_url,
            },
            status=status.HTTP_200_OK,
        )


class VendorOrderGenerateManifestView(APIView):
    """
    POST /api/shipping/vendor-orders/<vendor_order_number>/generate-manifest/
    Generates or retrieves pickup manifest PDF URL for a shipment.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, vendor_order_number):
        user = request.user
        is_admin = getattr(user, "is_staff", False) or getattr(user, "role", "") == "ADMIN"
        is_vendor = hasattr(user, "vendor_profile") and user.vendor_profile.status == "APPROVED"

        if not (is_admin or is_vendor):
            return Response(
                {"detail": "Approved vendor or admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_admin:
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number)
        else:
            vendor = user.vendor_profile
            vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number, vendor=vendor)

        if not hasattr(vendor_order, "shipment"):
            return Response(
                {"detail": "No shipment record exists for this vendor order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shipment = vendor_order.shipment

        try:
            manifest_url = ShippingService.generate_manifest(shipment)
        except ShippingConcurrencyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ShippingValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ShippingProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "success": True,
                "manifest_url": manifest_url,
            },
            status=status.HTTP_200_OK,
        )


class VendorOrderTrackingView(APIView):
    """
    GET /api/shipping/vendor-orders/<vendor_order_number>/tracking/
    Retrieves live tracking status and detailed history for a shipment.
    Accessible by owning vendor, ordering customer, or admin.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, vendor_order_number):
        user = request.user
        is_admin = getattr(user, "is_staff", False) or getattr(user, "role", "") == "ADMIN"
        is_vendor = hasattr(user, "vendor_profile") and user.vendor_profile.status == "APPROVED"

        vendor_order = get_object_or_404(VendorOrder, vendor_order_number=vendor_order_number)

        is_owner_vendor = is_vendor and vendor_order.vendor == user.vendor_profile
        is_customer = vendor_order.master_order.user == user

        if not (is_admin or is_owner_vendor or is_customer):
            return Response(
                {"detail": "You do not have permission to view tracking for this order."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not hasattr(vendor_order, "shipment"):
            return Response(
                {"detail": "No shipment record exists for this vendor order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        shipment = vendor_order.shipment

        try:
            shipment = ShippingService.track_shipment(shipment)
        except Exception as exc:
            logger.warning("Live provider tracking failed, returning stored shipment history: %s", exc)

        serializer = ShipmentSerializer(shipment)
        history_serializer = ShipmentStatusHistorySerializer(shipment.status_history.all(), many=True)

        return Response(
            {
                "success": True,
                "shipment": serializer.data,
                "history": history_serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class ShiprocketWebhookView(APIView):
    """
    POST /api/shipping/webhooks/shiprocket/
    Public webhook receiver endpoint for Shiprocket tracking events.
    Verifies configurable security token header and processes tracking events synchronously.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        header_name = getattr(settings, "SHIPROCKET_WEBHOOK_HEADER_NAME", "HTTP_X_SHIPROCKET_TOKEN")
        expected_secret = getattr(settings, "SHIPROCKET_WEBHOOK_SECRET", "")

        provided_token = request.headers.get(header_name) or request.META.get(header_name) or ""

        if expected_secret and (not provided_token or not hmac.compare_digest(provided_token, expected_secret)):
            logger.warning("Unauthorized Shiprocket webhook request (Invalid header token).")
            return Response(
                {"detail": "Unauthorized request. Security token invalid."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        payload = request.data
        if not isinstance(payload, dict):
            return Response(
                {"detail": "Invalid payload format. Expected JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        awb = payload.get("awb") or payload.get("awb_code")
        shipment_id = payload.get("shipment_id")

        shipment = None
        if awb:
            shipment = Shipment.objects.filter(awb_code=str(awb).strip()).first()
        if not shipment and shipment_id:
            shipment = Shipment.objects.filter(provider_shipment_id=str(shipment_id).strip()).first()

        if not shipment:
            logger.warning("Shiprocket webhook received for unknown AWB / Shipment ID: %s / %s", awb, shipment_id)
            return Response(
                {"status": "ignored", "detail": "Shipment not found in local system."},
                status=status.HTTP_200_OK,
            )

        try:
            shipment, event_category = ShippingService.process_tracking_event(shipment, payload)
        except Exception as exc:
            logger.exception("Error processing Shiprocket webhook tracking event: %s", exc)
            return Response(
                {"status": "error", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "status": "ok",
                "event_category": event_category,
                "shipment_status": shipment.status,
            },
            status=status.HTTP_200_OK,
        )

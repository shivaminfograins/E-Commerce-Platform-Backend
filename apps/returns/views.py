from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from .models import ReturnRequest, Refund
from .serializers import (
    ReturnRequestSerializer,
    CreateReturnRequestSerializer,
    RejectReturnSerializer,
    UpdateLogisticsSerializer,
    InspectReturnSerializer,
    ProcessRefundSerializer,
    RefundSerializer,
)
from .services import (
    create_return_request,
    approve_return_request,
    reject_return_request,
    update_logistics_status,
    inspect_return_item,
    process_return_refund,
)
from apps.vendors.permissions import IsApprovedVendor
from apps.admin_panel.permissions import AdminPermission


class CustomerReturnViewSet(viewsets.ModelViewSet):
    """
    Customer APIs for requesting, listing, viewing, and cancelling return requests.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ReturnRequestSerializer
    lookup_field = "return_number"

    def get_queryset(self):
        return ReturnRequest.objects.filter(customer=self.request.user).select_related(
            "order", "vendor_order", "order_item", "vendor", "refund"
        )

    def create(self, request, *args, **kwargs):
        serializer = CreateReturnRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            return_req = create_return_request(
                customer=request.user,
                order_item_id=serializer.validated_data["order_item_id"],
                quantity=serializer.validated_data["quantity"],
                reason=serializer.validated_data["reason"],
                customer_note=serializer.validated_data.get("customer_note", "")
            )
            out_serializer = ReturnRequestSerializer(return_req)
            return Response(out_serializer.data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def cancel(self, request, return_number=None):
        return_req = self.get_object()
        if return_req.status != ReturnRequest.REQUESTED:
            return Response(
                {"error": f"Cannot cancel return in status '{return_req.get_status_display()}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return_req.status = ReturnRequest.CANCELLED
        return_req.save(update_fields=["status", "updated_at"])
        out_serializer = ReturnRequestSerializer(return_req)
        return Response(out_serializer.data, status=status.HTTP_200_OK)


class VendorReturnViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Vendor APIs for viewing assigned returns, updating reverse logistics, and performing quality inspection.
    Enforces vendor isolation (returns HTTP 404 for unowned returns).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = ReturnRequestSerializer
    lookup_field = "return_number"

    def get_queryset(self):
        vendor = getattr(self.request.user, "vendor_profile", None)
        if not vendor:
            return ReturnRequest.objects.none()
        
        qs = ReturnRequest.objects.filter(vendor=vendor).select_related(
            "order", "vendor_order", "order_item", "vendor", "refund"
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, return_number=None):
        return_req = self.get_object()
        serializer = UpdateLogisticsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated_req = update_logistics_status(
                return_request_id=return_req.id,
                new_status=serializer.validated_data["status"],
                user=request.user
            )
            out_serializer = ReturnRequestSerializer(updated_req)
            return Response(out_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="inspect")
    def inspect(self, request, return_number=None):
        return_req = self.get_object()
        serializer = InspectReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            inspected_req = inspect_return_item(
                return_request_id=return_req.id,
                condition=serializer.validated_data["condition"],
                note=serializer.validated_data.get("note", ""),
                user=request.user
            )
            out_serializer = ReturnRequestSerializer(inspected_req)
            return Response(out_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AdminReturnViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Platform Admin APIs for reviewing, approving, rejecting, and refunding return requests.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AdminPermission]
    serializer_class = ReturnRequestSerializer

    def get_queryset(self):
        qs = ReturnRequest.objects.all().select_related(
            "order", "vendor_order", "order_item", "vendor", "refund", "customer"
        )
        vendor_id = self.request.query_params.get("vendor_id")
        status_param = self.request.query_params.get("status")
        customer_id = self.request.query_params.get("customer_id")

        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        if status_param:
            qs = qs.filter(status=status_param)
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        return qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            approved_req = approve_return_request(return_request_id=pk, admin_user=request.user)
            out_serializer = ReturnRequestSerializer(approved_req)
            return Response(out_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = RejectReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            rejected_req = reject_return_request(
                return_request_id=pk,
                rejection_reason=serializer.validated_data["rejection_reason"],
                admin_user=request.user
            )
            out_serializer = ReturnRequestSerializer(rejected_req)
            return Response(out_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        serializer = ProcessRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refund_obj = process_return_refund(
                return_request_id=pk,
                payout_reference=serializer.validated_data.get("payout_reference", ""),
                user=request.user
            )
            out_serializer = RefundSerializer(refund_obj)
            return Response(out_serializer.data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": e.message if hasattr(e, "message") else str(e)}, status=status.HTTP_400_BAD_REQUEST)

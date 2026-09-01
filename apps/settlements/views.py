from decimal import Decimal
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.admin_panel.permissions import AdminPermission
from apps.vendors.permissions import IsApprovedVendor
from apps.vendors.models import Vendor
from .models import VendorSettlement
from .serializers import (
    VendorSettlementSerializer,
    GenerateSettlementSerializer,
    ProcessPayoutSerializer,
    AddAdjustmentSerializer
)
from .services import (
    generate_vendor_settlements,
    submit_settlement_for_approval,
    approve_settlement,
    process_settlement_payout,
    add_settlement_adjustment
)


class AdminSettlementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Admin ViewSet for managing vendor settlements.
    Allows listing, viewing details, generating draft settlements, submitting, approving,
    processing payouts, adding adjustments, and cancelling statements.
    """
    permission_classes = [IsAuthenticated, AdminPermission]
    serializer_class = VendorSettlementSerializer
    queryset = VendorSettlement.objects.all().select_related("vendor", "approved_by").prefetch_related("items", "adjustments").order_by("-created_at")

    def get_queryset(self):
        qs = super().get_queryset()
        vendor_id = self.request.query_params.get("vendor_id")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())

        payout_status = self.request.query_params.get("payout_status")
        if payout_status:
            qs = qs.filter(payout_status=payout_status.upper())

        return qs

    @action(detail=False, methods=["post"], url_path="generate")
    def generate_settlement(self, request):
        serializer = GenerateSettlementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        vendor = get_object_or_404(Vendor, pk=data["vendor_id"])

        try:
            settlement = generate_vendor_settlements(
                vendor=vendor,
                period_start=data["period_start"],
                period_end=data["period_end"],
                user=request.user
            )
            return Response(
                VendorSettlementSerializer(settlement).data,
                status=status.HTTP_201_CREATED
            )
        except DjangoValidationError as e:
            raise ValidationError(detail=e.message if hasattr(e, "message") else str(e))

    @action(detail=True, methods=["post"], url_path="submit")
    def submit_for_approval(self, request, pk=None):
        settlement = self.get_object()
        try:
            updated = submit_settlement_for_approval(settlement, request.user)
            return Response(VendorSettlementSerializer(updated).data)
        except DjangoValidationError as e:
            raise ValidationError(detail=e.message if hasattr(e, "message") else str(e))

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        settlement = self.get_object()
        try:
            updated = approve_settlement(settlement, request.user)
            return Response(VendorSettlementSerializer(updated).data)
        except DjangoValidationError as e:
            raise ValidationError(detail=e.message if hasattr(e, "message") else str(e))

    @action(detail=True, methods=["post"], url_path="process-payout")
    def process_payout(self, request, pk=None):
        settlement = self.get_object()
        serializer = ProcessPayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated = process_settlement_payout(
                settlement=settlement,
                payout_reference=data["payout_reference"],
                payout_method=data.get("payout_method", "MANUAL_BANK_TRANSFER"),
                user=request.user
            )
            return Response(VendorSettlementSerializer(updated).data)
        except DjangoValidationError as e:
            raise ValidationError(detail=e.message if hasattr(e, "message") else str(e))

    @action(detail=True, methods=["post"], url_path="adjust")
    def add_adjustment(self, request, pk=None):
        settlement = self.get_object()
        serializer = AddAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            add_settlement_adjustment(
                settlement=settlement,
                adjustment_type=data["adjustment_type"],
                amount=data["amount"],
                reason=data["reason"],
                user=request.user
            )
            settlement.refresh_from_db()
            return Response(VendorSettlementSerializer(settlement).data)
        except DjangoValidationError as e:
            raise ValidationError(detail=e.message if hasattr(e, "message") else str(e))

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        settlement = self.get_object()
        if settlement.status in [VendorSettlement.SETTLED, VendorSettlement.CANCELLED]:
            raise ValidationError(f"Cannot cancel settlement in state '{settlement.status}'.")

        settlement.status = VendorSettlement.CANCELLED
        settlement.save(update_fields=["status", "updated_at"])
        return Response(VendorSettlementSerializer(settlement).data)


class VendorSettlementListView(generics.ListAPIView):
    """
    Returns paginated list of settlements for the authenticated approved vendor.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorSettlementSerializer

    def get_queryset(self):
        vendor = self.request.user.vendor_profile
        qs = VendorSettlement.objects.filter(vendor=vendor).select_related("vendor", "approved_by").prefetch_related("items", "adjustments").order_by("-created_at")
        
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())

        payout_status = self.request.query_params.get("payout_status")
        if payout_status:
            qs = qs.filter(payout_status=payout_status.upper())

        return qs


class VendorSettlementDetailView(generics.RetrieveAPIView):
    """
    Returns detail statement of a specific settlement belonging to the requesting vendor.
    Enforces strict vendor isolation (returns HTTP 404 for unowned settlements).
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorSettlementSerializer

    def get_queryset(self):
        vendor = self.request.user.vendor_profile
        return VendorSettlement.objects.filter(vendor=vendor).select_related("vendor", "approved_by").prefetch_related("items", "adjustments")


class VendorSettlementSummaryView(APIView):
    """
    Returns financial summary statistics for the requesting approved vendor.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def get(self, request):
        vendor = request.user.vendor_profile
        settlements = VendorSettlement.objects.filter(vendor=vendor)

        total_settled = settlements.filter(status=VendorSettlement.SETTLED).aggregate(total=Sum("net_payable"))["total"] or Decimal("0.00")
        pending_payout = settlements.filter(status=VendorSettlement.APPROVED, payout_status__in=[VendorSettlement.PAYOUT_UNPAID, VendorSettlement.PAYOUT_QUEUED, VendorSettlement.PAYOUT_PROCESSING]).aggregate(total=Sum("net_payable"))["total"] or Decimal("0.00")
        total_commission = settlements.filter(status=VendorSettlement.SETTLED).aggregate(total=Sum("commission_amount"))["total"] or Decimal("0.00")

        return Response({
            "success": True,
            "summary": {
                "total_settled_amount": str(total_settled),
                "pending_payout_amount": str(pending_payout),
                "total_commission_paid": str(total_commission),
            }
        })

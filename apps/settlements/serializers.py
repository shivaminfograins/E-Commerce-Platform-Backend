from decimal import Decimal
from rest_framework import serializers
from .models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment
from apps.vendors.models import Vendor


class VendorSettlementItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorSettlementItem
        fields = [
            "id",
            "vendor_order",
            "vendor_order_number_snapshot",
            "order_status_snapshot",
            "vendor_commission_rate_snapshot",
            "gross_amount",
            "discount_amount",
            "commission_amount",
            "net_payable",
            "created_at",
        ]
        read_only_fields = fields


class VendorSettlementAdjustmentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)

    class Meta:
        model = VendorSettlementAdjustment
        fields = [
            "id",
            "adjustment_type",
            "amount",
            "reason",
            "created_by",
            "created_by_email",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_by_email", "created_at"]


class VendorSettlementSerializer(serializers.ModelSerializer):
    items = VendorSettlementItemSerializer(many=True, read_only=True)
    adjustments = VendorSettlementAdjustmentSerializer(many=True, read_only=True)
    vendor_business_name = serializers.CharField(source="vendor.business_name", read_only=True)
    approved_by_email = serializers.EmailField(source="approved_by.email", read_only=True)

    class Meta:
        model = VendorSettlement
        fields = [
            "id",
            "settlement_number",
            "vendor",
            "vendor_business_name",
            "status",
            "payout_status",
            "period_start",
            "period_end",
            "gross_amount",
            "discount_amount",
            "tax_amount",
            "commission_amount",
            "adjustment_amount",
            "net_payable",
            "bank_account_holder_snapshot",
            "bank_name_snapshot",
            "bank_account_number_masked_snapshot",
            "ifsc_code_snapshot",
            "payout_reference",
            "payout_method",
            "approved_by",
            "approved_by_email",
            "approved_at",
            "settled_at",
            "notes",
            "items",
            "adjustments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class GenerateSettlementSerializer(serializers.Serializer):
    vendor_id = serializers.IntegerField(required=True)
    period_start = serializers.DateField(required=True)
    period_end = serializers.DateField(required=True)

    def validate(self, attrs):
        if attrs["period_start"] > attrs["period_end"]:
            raise serializers.ValidationError({"period_start": "Period start date cannot be after period end date."})
        return attrs


class ProcessPayoutSerializer(serializers.Serializer):
    payout_reference = serializers.CharField(max_length=100, required=True)
    payout_method = serializers.CharField(max_length=50, required=False, default="MANUAL_BANK_TRANSFER")


class AddAdjustmentSerializer(serializers.Serializer):
    adjustment_type = serializers.ChoiceField(choices=["CREDIT", "DEBIT"], required=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)
    reason = serializers.CharField(required=True)

    def validate_amount(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Amount must be greater than 0.00.")
        return value

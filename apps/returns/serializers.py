from rest_framework import serializers
from .models import ReturnRequest, Refund
from apps.orders.serializers import OrderItemSerializer


class RefundSerializer(serializers.ModelSerializer):
    refund_type_display = serializers.CharField(source="get_refund_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id",
            "refund_number",
            "amount",
            "refund_type",
            "refund_type_display",
            "status",
            "status_display",
            "gateway_refund_id",
            "payout_reference",
            "failure_reason",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class ReturnRequestSerializer(serializers.ModelSerializer):
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    inspection_condition_display = serializers.CharField(source="get_inspection_condition_display", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    vendor_order_number = serializers.CharField(source="vendor_order.vendor_order_number", read_only=True)
    vendor_store_name = serializers.CharField(source="vendor.store_name", read_only=True)
    order_item_details = OrderItemSerializer(source="order_item", read_only=True)
    refund = RefundSerializer(read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            "id",
            "return_number",
            "order",
            "order_number",
            "vendor_order",
            "vendor_order_number",
            "order_item",
            "order_item_details",
            "vendor",
            "vendor_store_name",
            "quantity",
            "reason",
            "reason_display",
            "customer_note",
            "status",
            "status_display",
            "inspection_condition",
            "inspection_condition_display",
            "inspection_note",
            "rejection_reason",
            "inventory_restored",
            "settlement_adjustment_applied",
            "requested_at",
            "approved_at",
            "received_at",
            "inspected_at",
            "completed_at",
            "refund",
        ]
        read_only_fields = fields


class CreateReturnRequestSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField(required=True)
    quantity = serializers.IntegerField(min_value=1, required=True)
    reason = serializers.ChoiceField(choices=ReturnRequest.REASON_CHOICES, required=True)
    customer_note = serializers.CharField(required=False, allow_blank=True, default="")


class RejectReturnSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=True, min_length=3)


class UpdateLogisticsSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            ReturnRequest.PICKUP_PENDING,
            ReturnRequest.IN_TRANSIT,
            ReturnRequest.RECEIVED,
        ],
        required=True
    )


class InspectReturnSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=ReturnRequest.CONDITION_CHOICES, required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ProcessRefundSerializer(serializers.Serializer):
    payout_reference = serializers.CharField(required=False, allow_blank=True, default="")

"""
apps/orders/serializers.py
==========================
"""

from decimal import Decimal
from rest_framework import serializers

from .models import Order, OrderItem, VendorOrder
from apps.coupons.serializers import CouponSerializer


# ===========================================================================
# INPUT — PlaceOrderSerializer
# ===========================================================================
class PlaceOrderSerializer(serializers.Serializer):
    """
    Validates the POST /api/orders/ request body.
    """

    address = serializers.IntegerField(
        help_text="Primary key of the delivery Address owned by the requesting user.",
    )

    payment_method = serializers.ChoiceField(
        choices=Order.PAYMENT_METHOD_CHOICES,
        help_text="Payment gateway or method chosen at checkout.",
    )

    coupon_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=50,
        help_text="Optional coupon / promo code.",
    )

    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional delivery instructions or customer notes.",
    )

    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        help_text="Unique client-generated key to ensure idempotency."
    )


# ===========================================================================
# OUTPUT — OrderItemSerializer
# ===========================================================================
class OrderItemSerializer(serializers.ModelSerializer):
    """
    Serialises one line item inside an Order detail response.
    """

    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
        allow_null=True,
    )
    variant_id = serializers.IntegerField(
        source="variant.id",
        read_only=True,
        allow_null=True,
    )
    vendor_order_id = serializers.IntegerField(
        source="vendor_order.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model  = OrderItem
        fields = (
            "id",
            "product_id",
            "variant_id",
            "vendor_order_id",
            "product_name",
            "variant_name",
            "sku",
            "product_image",
            "price",
            "quantity",
            "total",
        )
        read_only_fields = fields


# ===========================================================================
# OUTPUT — OrderSummarySerializer  (list endpoint)
# ===========================================================================
class OrderSummarySerializer(serializers.ModelSerializer):
    """
    Lightweight serialiser used by GET /api/orders/ (list).
    """

    status_display         = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )
    payment_status_display = serializers.CharField(
        source="get_payment_status_display",
        read_only=True,
    )
    payment_method_display = serializers.CharField(
        source="get_payment_method_display",
        read_only=True,
    )

    item_count     = serializers.IntegerField(read_only=True)
    is_cancellable = serializers.BooleanField(read_only=True)

    delivery_address = serializers.SerializerMethodField()

    first_item_image  = serializers.SerializerMethodField()
    first_item_name   = serializers.SerializerMethodField()
    razorpay_order_id = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    
    coupon = CouponSerializer(read_only=True)
    discount_amount = serializers.DecimalField(source="discount", max_digits=10, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(source="total_amount", max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model  = Order
        fields = (
            "id",
            "order_number",
            "status",
            "status_display",
            "payment_method",
            "payment_method_display",
            "payment_status",
            "payment_status_display",
            "subtotal",
            "shipping_charge",
            "discount",
            "discount_amount",
            "tax",
            "total_amount",
            "grand_total",
            "item_count",
            "is_cancellable",
            "delivery_address",
            "first_item_image",
            "first_item_name",
            "razorpay_order_id",
            "coupon",
            "coupon_code",
            "notes",
            "user_email",
            "user_username",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_delivery_address(self, obj):
        return {
            "full_name":      obj.snapshot_full_name,
            "phone":          obj.snapshot_phone,
            "address_line_1": obj.snapshot_address_line_1,
            "address_line_2": obj.snapshot_address_line_2,
            "landmark":       obj.snapshot_landmark,
            "city":           obj.snapshot_city,
            "state":          obj.snapshot_state,
            "country":        obj.snapshot_country,
            "postal_code":    obj.snapshot_postal_code,
        }

    def get_first_item_image(self, obj):
        first_item = obj.items.first()
        return first_item.product_image if first_item else ""

    def get_first_item_name(self, obj):
        first_item = obj.items.first()
        return first_item.product_name if first_item else "Order Items"

    def get_razorpay_order_id(self, obj):
        last_txn = obj.transactions.filter(payment_method="razorpay", status="pending").first()
        return last_txn.razorpay_order_id if last_txn else ""


# ===========================================================================
# VENDOR ORDER SERIALIZERS
# ===========================================================================
class VendorOrderSummarySerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    master_order_number = serializers.CharField(source="master_order.order_number", read_only=True)
    item_count = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="master_order.snapshot_full_name", read_only=True)
    customer_phone = serializers.CharField(source="master_order.snapshot_phone", read_only=True)
    delivery_city = serializers.CharField(source="master_order.snapshot_city", read_only=True)

    class Meta:
        model = VendorOrder
        fields = (
            "id",
            "vendor_order_number",
            "master_order_number",
            "status",
            "status_display",
            "subtotal",
            "shipping_charge",
            "discount",
            "tax",
            "total_amount",
            "item_count",
            "customer_name",
            "customer_phone",
            "delivery_city",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_item_count(self, obj):
        return obj.items.count()


class VendorOrderSerializer(VendorOrderSummarySerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_address = serializers.SerializerMethodField()
    master_order_status = serializers.CharField(source="master_order.status", read_only=True)
    master_order_payment_status = serializers.CharField(source="master_order.payment_status", read_only=True)

    class Meta(VendorOrderSummarySerializer.Meta):
        fields = VendorOrderSummarySerializer.Meta.fields + (
            "items",
            "delivery_address",
            "master_order_status",
            "master_order_payment_status",
            "tracking_number",
            "carrier_name",
            "shipped_at",
            "delivered_at",
            "vendor_store_name_snapshot",
            "vendor_business_name_snapshot",
        )
        read_only_fields = fields

    def get_delivery_address(self, obj):
        mo = obj.master_order
        return {
            "full_name":      mo.snapshot_full_name,
            "phone":          mo.snapshot_phone,
            "address_line_1": mo.snapshot_address_line_1,
            "address_line_2": mo.snapshot_address_line_2,
            "landmark":       mo.snapshot_landmark,
            "city":           mo.snapshot_city,
            "state":          mo.snapshot_state,
            "country":        mo.snapshot_country,
            "postal_code":    mo.snapshot_postal_code,
        }


# ===========================================================================
# OUTPUT — OrderSerializer  (detail endpoint)
# ===========================================================================
class OrderSerializer(OrderSummarySerializer):
    """
    Full order detail serialiser.
    """

    items = OrderItemSerializer(many=True, read_only=True)
    vendor_orders = VendorOrderSerializer(many=True, read_only=True)

    class Meta(OrderSummarySerializer.Meta):
        fields = OrderSummarySerializer.Meta.fields + (
            "snapshot_full_name",
            "snapshot_phone",
            "snapshot_address_line_1",
            "snapshot_address_line_2",
            "snapshot_landmark",
            "snapshot_city",
            "snapshot_state",
            "snapshot_country",
            "snapshot_postal_code",
            "items",
            "vendor_orders",
        )
        read_only_fields = fields
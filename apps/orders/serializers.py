"""
apps/orders/serializers.py
==========================

Serializer map
--------------

  INPUT (write)
  ─────────────
  PlaceOrderSerializer   — POST /api/orders/           body validation

  OUTPUT (read)
  ─────────────
  OrderItemSerializer    — one line item (snapshot + live FK ids)
  OrderSummarySerializer — lightweight list row (no nested items)
  OrderSerializer        — full detail (nested items + address snapshot)

Design rules
------------
* Plain `Serializer` for input → explicit field set, no mass-assignment.
* `ModelSerializer` for output → DRF generates field mapping automatically,
  we add computed/nested fields on top.
* All output serializers mark every field read_only so they can never be
  accidentally used as input.
* `SerializerMethodField` for the formatted delivery address avoids shipping
  nine separate snapshot fields to the list endpoint (bandwidth saving).
"""

from rest_framework import serializers

from .models import Order, OrderItem


# ===========================================================================
# INPUT — PlaceOrderSerializer
# ===========================================================================
class PlaceOrderSerializer(serializers.Serializer):
    """
    Validates the POST /api/orders/ request body.

    Accepted payload
    ----------------
    {
        "address":        12,              ← Address PK  (required, integer)
        "payment_method": "cod",           ← Order.PAYMENT_METHOD_CHOICES
        "coupon_code":    "SAVE10",        ← optional, max 50 chars
        "notes":          "Leave at door"  ← optional, free text
    }

    Using a plain Serializer (not ModelSerializer) guarantees that only
    these four fields are accepted — no accidental write-through to the
    Order model's other fields.
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


# ===========================================================================
# OUTPUT — OrderItemSerializer
# ===========================================================================
class OrderItemSerializer(serializers.ModelSerializer):
    """
    Serialises one line item inside an Order detail response.

    All data is read-only snapshot data that was frozen at checkout.
    The live catalogue FK ids (product_id, variant_id) are included so
    the frontend can optionally deep-link to the current product page —
    they will be null if the product/variant was removed from the catalogue
    after the order was placed, which is fine because the snapshot fields
    always contain the authoritative purchase record.
    """

    # Nullable: SET_NULL means these become None when catalogue entry deleted
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

    class Meta:
        model  = OrderItem
        fields = (
            "id",
            # ── Live catalogue references (deep-link helpers) ──────────
            "product_id",
            "variant_id",
            # ── Immutable purchase snapshot ────────────────────────────
            "product_name",
            "variant_name",
            "sku",
            "product_image",
            # ── Financial snapshot ─────────────────────────────────────
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

    Does NOT include the nested items array — fetching 20 orders with
    their items would be expensive and the list view doesn't need them.
    The frontend can call GET /api/orders/<id>/ for full item detail.

    Includes:
      • order_number, status, payment_status, payment_method
      • total_amount, item_count
      • formatted delivery address (single string, not 9 separate fields)
      • created_at (for date display and sorting)
      • is_cancellable (so the UI can show/hide Cancel button per row)
    """

    # ── Human-readable labels for choice fields ────────────────────────────
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

    # ── Computed ───────────────────────────────────────────────────────────
    item_count     = serializers.IntegerField(read_only=True)
    is_cancellable = serializers.BooleanField(read_only=True)

    # ── Single-string address (saves bandwidth vs 9 separate fields) ───────
    delivery_address = serializers.SerializerMethodField()

    # ── Preview fields for list views ──────────────────────────────────────
    first_item_image = serializers.SerializerMethodField()
    first_item_name  = serializers.SerializerMethodField()

    class Meta:
        model  = Order
        fields = (
            "id",
            "order_number",
            # Status
            "status",
            "status_display",
            "payment_method",
            "payment_method_display",
            "payment_status",
            "payment_status_display",
            # Financial
            "subtotal",
            "shipping_charge",
            "discount",
            "tax",
            "total_amount",
            # Convenience
            "item_count",
            "is_cancellable",
            "delivery_address",
            "first_item_image",
            "first_item_name",
            "razorpay_order_id",
            "coupon_code",
            "notes",
            # Timestamps
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_delivery_address(self, obj):
        """
        Returns a compact dict of the snapshot address fields.
        Using a dict (not a formatted string) lets the frontend
        render each field independently if needed.
        """
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
        # Retrieve the latest pending Razorpay transaction associated with this order
        last_txn = obj.transactions.filter(payment_method="razorpay", status="pending").first()
        return last_txn.razorpay_order_id if last_txn else ""


# ===========================================================================
# OUTPUT — OrderSerializer  (detail endpoint)
# ===========================================================================
class OrderSerializer(OrderSummarySerializer):
    """
    Full order detail serialiser used by:
      • POST /api/orders/        (create response)
      • GET  /api/orders/<id>/   (detail response)
      • PATCH /api/orders/<id>/cancel/  (cancel response)

    Inherits all fields from OrderSummarySerializer and adds:
      • items  — nested list of OrderItemSerializer
      • The nine raw snapshot address fields (for invoice rendering)

    Inheriting from OrderSummarySerializer avoids duplicating the
    computed fields (status_display, item_count, delivery_address, etc.).
    """

    items = OrderItemSerializer(many=True, read_only=True)

    class Meta(OrderSummarySerializer.Meta):
        fields = OrderSummarySerializer.Meta.fields + (
            # ── Raw snapshot fields (for invoice / packing slip) ───────
            "snapshot_full_name",
            "snapshot_phone",
            "snapshot_address_line_1",
            "snapshot_address_line_2",
            "snapshot_landmark",
            "snapshot_city",
            "snapshot_state",
            "snapshot_country",
            "snapshot_postal_code",
            # ── Nested line items ──────────────────────────────────────
            "items",
        )
        read_only_fields = fields
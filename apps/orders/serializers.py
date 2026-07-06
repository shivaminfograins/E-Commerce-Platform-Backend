from rest_framework import serializers

from .models import (
    Order,
    OrderItem,
)


# ==========================================================
# Order Item Serializer
# Used inside Order Detail API
# ==========================================================
class OrderItemSerializer(serializers.ModelSerializer):

    class Meta:

        model = OrderItem

        fields = (
            "id",
            "product",
            "product_name",
            "product_image",
            "product_sku",
            "price",
            "quantity",
            "total",
        )


# ==========================================================
# Order Serializer
# Used for Order Detail API
# ==========================================================
class OrderSerializer(serializers.ModelSerializer):

    items = OrderItemSerializer(
        many=True,
        read_only=True
    )

    class Meta:

        model = Order

        fields = (
            "id",
            "order_number",
            "status",
            "payment_method",
            "payment_status",

            "full_name",
            "phone",

            "address_line_1",
            "address_line_2",
            "landmark",

            "city",
            "state",
            "country",
            "postal_code",

            "subtotal",
            "shipping_charge",
            "discount",
            "tax",
            "total_amount",

            "notes",

            "created_at",

            "items",
        )

# ==========================================================
# Place Order Serializer
#
# Used only in
# POST /api/orders/
# ==========================================================
class PlaceOrderSerializer(serializers.Serializer):

    address = serializers.IntegerField()

    payment_method = serializers.ChoiceField(
        choices=Order.PAYMENT_METHOD_CHOICES
    )

    notes = serializers.CharField(
        required=False,
        allow_blank=True
    )
from rest_framework import serializers
from .models import CartItem, Guest
from apps.products.models import ProductVariant


# ----------------------------------------------------------
# CartItemSerializer  (READ — returned in every cart response)
#
# Traversal path for each CartItem row:
#   CartItem
#     └── variant  (ProductVariant)
#           ├── product  (Product)
#           │     └── images  (ProductImage QuerySet)
#           ├── name
#           ├── sku
#           ├── price
#           └── stock
#
# select_related("variant__product") in the view means all of the
# above is resolved in a single SQL JOIN — no extra queries per row.
# ----------------------------------------------------------
class CartItemSerializer(serializers.ModelSerializer):

    # ── Product-level fields ──────────────────────────────────
    # Pulled one level up from variant.product so the frontend
    # doesn't have to dig into a nested object.
    product_id = serializers.IntegerField(
        source="variant.product.id",
        read_only=True,
    )
    product_name = serializers.CharField(
        source="variant.product.name",
        read_only=True,
    )

    # ── Variant-level fields ──────────────────────────────────
    variant_id = serializers.IntegerField(
        source="variant.id",
        read_only=True,
    )
    variant_name = serializers.CharField(
        source="variant.name",
        read_only=True,
    )
    sku = serializers.CharField(
        source="variant.sku",
        read_only=True,
    )
    price = serializers.DecimalField(
        source="variant.price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    stock = serializers.IntegerField(
        source="variant.stock",
        read_only=True,
    )

    # ── Product image ─────────────────────────────────────────
    # Returns the URL of the first image attached to the parent product.
    # Falls back to None if no image exists.
    image = serializers.SerializerMethodField()

    # ── Computed subtotal ─────────────────────────────────────
    # price × quantity, calculated in Python (no extra DB hit).
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            # product context
            "product_id",
            "product_name",
            # variant details
            "variant_id",
            "variant_name",
            "sku",
            "price",
            "stock",
            # image
            "image",
            # cart-specific
            "quantity",
            "subtotal",
            # timestamps
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields  # everything is read-only in the response

    def get_image(self, obj):
        """
        Return the URL of the first ProductVariantImage for the variant.
        Requires select_related("variant") + prefetch_related("variant__images")
        in the view queryset to avoid N+1 queries.
        """
        request = self.context.get("request")
        first_image = obj.variant.images.first()
        if first_image and first_image.image:
            if request:
                # Build an absolute URL (includes http://host)
                return request.build_absolute_uri(first_image.image.url)
            return first_image.image.url
        return None

    def get_subtotal(self, obj):
        """
        Price × quantity, rounded to 2 decimal places.
        Returns a float so it serialises cleanly to JSON.
        """
        return round(float(obj.variant.price) * obj.quantity, 2)


# ----------------------------------------------------------
# CartItemInputSerializer  (WRITE — validates the request body)
#
# Expected payload:
#   {
#       "variant": 15,
#       "quantity": 2
#   }
# ----------------------------------------------------------
class CartItemInputSerializer(serializers.Serializer):

    variant = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.select_related("product").filter(is_active=True),
        help_text="Primary key of the ProductVariant to add to the cart.",
    )
    quantity = serializers.IntegerField(
        default=1,
        min_value=1,
        help_text="Number of units to add (must be ≥ 1).",
    )

    def validate_variant(self, variant):
        """
        Extra guard: reject variants whose parent product is inactive.
        The queryset above already filters is_active on the variant itself;
        this check covers the parent product.
        """
        if not variant.product.is_active:
            raise serializers.ValidationError(
                "This product is no longer available."
            )
        return variant

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value

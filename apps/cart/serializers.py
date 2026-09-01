from rest_framework import serializers
from .models import CartItem, Guest
from apps.products.models import ProductVariant
from apps.vendors.models import VendorProduct, VendorProductVariant


# ----------------------------------------------------------
# CartItemSerializer  (READ — returned in every cart response)
# ----------------------------------------------------------
class CartItemSerializer(serializers.ModelSerializer):

    vendor_product_id = serializers.IntegerField(
        source="vendor_product.id",
        read_only=True,
        allow_null=True,
    )
    vendor_product_variant_id = serializers.IntegerField(
        source="vendor_product_variant.id",
        read_only=True,
        allow_null=True,
    )
    vendor_id = serializers.IntegerField(
        source="vendor_product.vendor.id",
        read_only=True,
    )
    vendor_name = serializers.CharField(
        source="vendor_product.vendor.store_name",
        read_only=True,
    )

    product_id = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    variant_id = serializers.SerializerMethodField()
    variant_name = serializers.SerializerMethodField()
    sku = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    stock = serializers.SerializerMethodField()

    image = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "vendor_id",
            "vendor_name",
            "vendor_product_id",
            "vendor_product_variant_id",
            "product_id",
            "product_name",
            "variant_id",
            "variant_name",
            "sku",
            "price",
            "stock",
            "image",
            "quantity",
            "subtotal",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _get_vpv(self, obj):
        if not obj.vendor_product_variant_id:
            return None
        try:
            return obj.vendor_product_variant
        except Exception:
            return None

    def _get_vp(self, obj):
        if not obj.vendor_product_id:
            return None
        try:
            return obj.vendor_product
        except Exception:
            return None

    def get_product_id(self, obj):
        vp = self._get_vp(obj)
        return vp.product.id if vp and vp.product else None

    def get_product_name(self, obj):
        vp = self._get_vp(obj)
        return vp.product.name if vp and vp.product else ""

    def get_variant_id(self, obj):
        vpv = self._get_vpv(obj)
        return vpv.product_variant.id if vpv and vpv.product_variant else None

    def get_variant_name(self, obj):
        vpv = self._get_vpv(obj)
        return vpv.product_variant.name if vpv and vpv.product_variant else ""

    def get_sku(self, obj):
        vpv = self._get_vpv(obj)
        if vpv:
            if getattr(vpv, "vendor_sku", None):
                return vpv.vendor_sku
            if vpv.product_variant:
                return vpv.product_variant.sku
        vp = self._get_vp(obj)
        if vp and vp.product:
            v_first = vp.product.variants.first()
            if v_first and v_first.sku:
                return v_first.sku
            return vp.product.slug or ""
        return ""

    def get_price(self, obj):
        vpv = self._get_vpv(obj)
        if vpv:
            return str(vpv.price)
        vp = self._get_vp(obj)
        return str(vp.price) if vp else "0.00"

    def get_stock(self, obj):
        vpv = self._get_vpv(obj)
        if vpv:
            return vpv.stock
        vp = self._get_vp(obj)
        return vp.stock if vp else 0

    def get_image(self, obj):
        request = self.context.get("request")
        first_image = None
        vpv = self._get_vpv(obj)
        if vpv and vpv.product_variant:
            first_image = vpv.product_variant.images.first()
        vp = self._get_vp(obj)
        if not first_image and vp and vp.product:
            v_first = vp.product.variants.first()
            if v_first:
                first_image = v_first.images.first()
            
        if first_image and first_image.image:
            if request:
                return request.build_absolute_uri(first_image.image.url)
            return first_image.image.url
        return None

    def get_subtotal(self, obj):
        price_str = self.get_price(obj)
        return round(float(price_str) * obj.quantity, 2)


# ----------------------------------------------------------
# CartItemInputSerializer  (WRITE — validates the request body)
# ----------------------------------------------------------
class CartItemInputSerializer(serializers.Serializer):

    vendor_product = serializers.PrimaryKeyRelatedField(
        queryset=VendorProduct.objects.filter(status="ACTIVE", vendor__status="APPROVED"),
        required=False,
        allow_null=True
    )
    vendor_product_variant = serializers.PrimaryKeyRelatedField(
        queryset=VendorProductVariant.objects.filter(is_active=True, vendor_product__status="ACTIVE", vendor_product__vendor__status="APPROVED"),
        required=False,
        allow_null=True
    )
    quantity = serializers.IntegerField(
        default=1,
        min_value=1,
        help_text="Number of units to add (must be ≥ 1).",
    )

    def validate(self, data):
        vp = data.get("vendor_product")
        vpv = data.get("vendor_product_variant")
        
        if vpv:
            if vp and vpv.vendor_product != vp:
                raise serializers.ValidationError("vendor_product_variant must belong to the selected vendor_product.")
            if not vp:
                data["vendor_product"] = vpv.vendor_product
        elif vp:
            if vp.product.variants.exists():
                raise serializers.ValidationError("This listing has variants; vendor_product_variant must be specified.")
        else:
            raise serializers.ValidationError("A vendor offer (product or variant) must be specified.")
            
        return data

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value

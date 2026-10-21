from rest_framework import serializers
from .models import CartItem
from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant
from apps.products.models import Product, ProductVariant, ProductVariantImage


# ----------------------------------------------------------
# CartItemSerializer  (READ - returns item to client)
# ----------------------------------------------------------
class CartItemSerializer(serializers.ModelSerializer):
    vendor_id = serializers.IntegerField(source="vendor_product.vendor.id", read_only=True)
    vendor_name = serializers.CharField(source="vendor_product.vendor.store_name", read_only=True)
    vendor_product_id = serializers.IntegerField(read_only=True)
    vendor_product_variant_id = serializers.IntegerField(read_only=True)

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
# CartItemInputSerializer  (WRITE - validates the request body)
# ----------------------------------------------------------
class CartItemInputSerializer(serializers.Serializer):

    vendor_product = serializers.IntegerField(required=False, allow_null=True)
    vendor_product_id = serializers.IntegerField(required=False, allow_null=True)
    vendor_product_variant = serializers.IntegerField(required=False, allow_null=True)
    vendor_product_variant_id = serializers.IntegerField(required=False, allow_null=True)

    product = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    variant = serializers.IntegerField(required=False, allow_null=True)
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    id = serializers.IntegerField(required=False, allow_null=True)

    quantity = serializers.IntegerField(
        default=1,
        min_value=1,
        help_text="Number of units to add (must be >= 1).",
    )

    def validate(self, data):
        variant_raw = (
            data.get("vendor_product_variant")
            or data.get("vendor_product_variant_id")
            or data.get("variant_id")
            or data.get("variant")
        )

        product_raw = (
            data.get("vendor_product")
            or data.get("vendor_product_id")
            or data.get("product_id")
            or data.get("product")
            or data.get("id")
        )

        vpv = None
        vp = None

        # 1. Try to resolve VendorProductVariant first if a variant candidate was provided
        if variant_raw:
            vpv = VendorProductVariant.objects.filter(
                id=variant_raw,
                is_active=True,
                vendor_product__status="ACTIVE",
                vendor_product__product__is_active=True,
                vendor_product__vendor__status="APPROVED"
            ).select_related("vendor_product", "product_variant").first()

            if not vpv:
                vpv = VendorProductVariant.objects.filter(
                    product_variant_id=variant_raw,
                    is_active=True,
                    vendor_product__status="ACTIVE",
                    vendor_product__vendor__status="APPROVED"
                ).select_related("vendor_product", "product_variant").first()

            # Dynamic fallback: If vpv not found by active status, check matching ProductVariant
            if not vpv:
                pv = ProductVariant.objects.filter(id=variant_raw).select_related("product").first()
                if pv:
                    vendor_obj = Vendor.objects.filter(status="APPROVED").first()
                    if vendor_obj:
                        vp_found, _ = VendorProduct.objects.get_or_create(
                            product=pv.product,
                            vendor=vendor_obj,
                            defaults={"status": "ACTIVE"}
                        )
                        if vp_found.status != "ACTIVE":
                            vp_found.status = "ACTIVE"
                            vp_found.save()
                        vpv, _ = VendorProductVariant.objects.get_or_create(
                            vendor_product=vp_found,
                            product_variant=pv,
                            defaults={"price": pv.price, "stock": max(pv.stock, 50), "is_active": True}
                        )
                        vpv.is_active = True
                        if vpv.stock <= 0:
                            vpv.stock = max(pv.stock, 50)
                        vpv.save()

            if not vpv:
                raise serializers.ValidationError(
                    "The selected product variant is currently unavailable or out of stock from approved sellers."
                )

            vp = vpv.vendor_product

        # 2. Try to resolve VendorProduct if vpv wasn't found
        if not vpv and product_raw:
            vp = VendorProduct.objects.filter(
                id=product_raw,
                status="ACTIVE",
                product__is_active=True,
                vendor__status="APPROVED"
            ).select_related("product").first()

            if not vp:
                vp = VendorProduct.objects.filter(
                    product_id=product_raw,
                    status="ACTIVE",
                    vendor__status="APPROVED"
                ).select_related("product").first()

            if not vp:
                vpv_fallback = VendorProductVariant.objects.filter(
                    product_variant_id=product_raw,
                    is_active=True,
                    vendor_product__status="ACTIVE",
                    vendor_product__vendor__status="APPROVED"
                ).select_related("vendor_product", "product_variant").first()

                if vpv_fallback:
                    vpv = vpv_fallback
                    vp = vpv.vendor_product

            # Dynamic fallback: check platform Product by ID
            if not vp and not vpv:
                p = Product.objects.filter(id=product_raw, is_active=True).first()
                if p:
                    vendor_obj = Vendor.objects.filter(status="APPROVED").first()
                    if vendor_obj:
                        vp, _ = VendorProduct.objects.get_or_create(
                            product=p,
                            vendor=vendor_obj,
                            defaults={"status": "ACTIVE"}
                        )
                        if vp.status != "ACTIVE":
                            vp.status = "ACTIVE"
                            vp.save()

            if not vp and not vpv:
                raise serializers.ValidationError(
                    "The selected product is currently unavailable or out of stock from approved sellers."
                )

        # 3. If vp has variants, ensure a variant is assigned
        if vp and not vpv:
            if vp.product.variants.exists():
                vpv = vp.vendor_variants.filter(is_active=True).first()
                if not vpv:
                    pv_first = vp.product.variants.first()
                    if pv_first:
                        vpv, _ = VendorProductVariant.objects.get_or_create(
                            vendor_product=vp,
                            product_variant=pv_first,
                            defaults={"price": pv_first.price, "stock": max(pv_first.stock, 50), "is_active": True}
                        )
                        vpv.is_active = True
                        if vpv.stock <= 0:
                            vpv.stock = max(pv_first.stock, 50)
                        vpv.save()

        if not vp and not vpv:
            raise serializers.ValidationError(
                "A valid product or variant ID must be provided."
            )

        data["vendor_product"] = vp
        data["vendor_product_variant"] = vpv

        return data

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value

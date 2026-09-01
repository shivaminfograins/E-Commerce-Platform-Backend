from rest_framework import serializers
from django.urls import reverse
from django.contrib.auth import get_user_model
from .models import Vendor, VendorAddress, VendorDocument, VendorBankAccount

User = get_user_model()


class VendorAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorAddress
        fields = [
            "id", "address_type", "full_name", "phone",
            "address_line_1", "address_line_2", "city",
            "state", "country", "postal_code"
        ]


class VendorDocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = VendorDocument
        fields = [
            "id", "document_type", "document_file", "document_number",
            "verification_status", "rejection_reason", "download_url"
        ]
        read_only_fields = ["verification_status", "rejection_reason", "download_url"]
        extra_kwargs = {
            "document_file": {"write_only": True}
        }

    def get_download_url(self, obj):
        request = self.context.get("request")
        url_path = reverse("vendor-document-download", kwargs={"pk": obj.id})
        if request:
            url = request.build_absolute_uri(url_path)
            if request.user and request.user.is_authenticated:
                token = getattr(request, "auth", None)
                if token:
                    url = f"{url}?token={token}"
                else:
                    auth_header = request.headers.get("Authorization", "")
                    if auth_header.startswith("Bearer "):
                        raw_token = auth_header.split(" ")[1]
                        url = f"{url}?token={raw_token}"
            return url
        return url_path


class VendorBankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorBankAccount
        fields = [
            "account_holder_name", "account_number", "bank_name",
            "branch_name", "ifsc_code"
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Mask the account number for security in read representation
        acc_num = data.get("account_number")
        if acc_num:
            if len(acc_num) > 4:
                data["account_number"] = "*" * (len(acc_num) - 4) + acc_num[-4:]
            else:
                data["account_number"] = "****"
        return data

    def validate_account_number(self, value):
        # A masked string (containing '*') must never be accepted as a valid write value
        if "*" in value:
            raise serializers.ValidationError("Invalid account number format. Masked values are read-only.")
        if not value.isdigit():
            raise serializers.ValidationError("Account number must contain digits only.")
        return value


class VendorSerializer(serializers.ModelSerializer):
    addresses = VendorAddressSerializer(many=True, read_only=True)
    documents = VendorDocumentSerializer(many=True, read_only=True)
    bank_account = VendorBankAccountSerializer(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id", "user", "business_name", "store_name",
            "business_email", "business_phone", "status",
            "commission_rate", "rejection_reason", "created_at",
            "updated_at", "addresses", "documents", "bank_account"
        ]
        read_only_fields = [
            "id", "user", "status", "commission_rate",
            "rejection_reason", "created_at", "updated_at"
        ]


class VendorStoreProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer used specifically for APPROVED vendors updating their store details.
    Only allows store_name, business_name, business_email, business_phone, and address fields.
    If bank account, documents, status, commission_rate, or other forbidden fields are sent,
    we explicitly raise ValidationError (400).
    """
    class Meta:
        model = Vendor
        fields = [
            "business_name", "store_name", "business_email", "business_phone"
        ]

    def validate(self, attrs):
        # The request payload itself is in self.initial_data
        request = self.context.get("request")
        if request:
            payload = request.data
            forbidden_fields = [
                "status", "commission_rate", "rejection_reason", "user", "id", "created_at", "updated_at",
                "bank_account", "bank_account_holder_name", "bank_account_number", "bank_name", "bank_branch_name", "bank_ifsc_code",
                "documents", "document_type", "document_file", "document_number", "verification_status"
            ]
            # Also check files
            uploaded_doc_keys = [k for k in request.FILES.keys() if k.startswith("document_file_")]
            if uploaded_doc_keys:
                raise serializers.ValidationError({"documents": "Approved vendors cannot update verification documents."})

            for field in forbidden_fields:
                if field in payload:
                    raise serializers.ValidationError({field: f"Updating '{field}' is forbidden for approved vendors."})
        return attrs


from apps.products.models import Product, ProductVariant
from .models import VendorProduct, VendorProductVariant
from apps.products.serializers import ProductVariantSerializer

class VendorCatalogProductSerializer(serializers.ModelSerializer):
    has_variants = serializers.SerializerMethodField()
    already_listed = serializers.SerializerMethodField()
    variants = ProductVariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True, default="")

    class Meta:
        model = Product
        fields = [
            "id", "name", "slug", "description", "is_active", 
            "category", "category_name", "brand", "brand_name", 
            "has_variants", "already_listed", "variants"
        ]

    def get_has_variants(self, obj):
        return obj.variants.exists()

    def get_already_listed(self, obj):
        request = self.context.get("request")
        if request and request.user and hasattr(request.user, "vendor_profile"):
            return VendorProduct.objects.filter(
                vendor=request.user.vendor_profile,
                product=obj
            ).exists()
        return False


class VendorProductVariantSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="product_variant.name", read_only=True)
    variant_sku = serializers.CharField(source="product_variant.sku", read_only=True)

    class Meta:
        model = VendorProductVariant
        fields = [
            "id", "vendor_product", "product_variant", "variant_name", "variant_sku", 
            "price", "stock", "vendor_sku", "is_active", "created_at", "updated_at"
        ]
        read_only_fields = ["id", "vendor_product", "created_at", "updated_at"]


class VendorProductSerializer(serializers.ModelSerializer):
    vendor_variants = VendorProductVariantSerializer(many=True, required=False)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    product_description = serializers.CharField(source="product.description", read_only=True)
    category_name = serializers.CharField(source="product.category.name", read_only=True)
    master_variants = serializers.SerializerMethodField()

    class Meta:
        model = VendorProduct
        fields = [
            "id", "vendor", "product", "product_name", "product_slug", "product_description", 
            "category_name", "status", "price", "stock", "vendor_variants", "master_variants", 
            "created_at", "updated_at"
        ]
        read_only_fields = ["id", "vendor", "created_at", "updated_at"]

    def get_master_variants(self, obj):
        return ProductVariantSerializer(obj.product.variants.all(), many=True, context=self.context).data

    def validate(self, attrs):
        status = attrs.get("status")
        price = attrs.get("price")
        stock = attrs.get("stock")
        instance = self.instance
        
        product = attrs.get("product")
        if not product and instance:
            product = instance.product

        if instance:
            if "product" in attrs and attrs["product"] != instance.product:
                raise serializers.ValidationError({"product": "Product is immutable after listing creation."})

        if status:
            if status == "SUSPENDED":
                if not instance or instance.status != "SUSPENDED":
                    raise serializers.ValidationError({"status": "Vendors cannot explicitly set status to SUSPENDED."})
            
            if instance and instance.status == "SUSPENDED" and status != "SUSPENDED":
                raise serializers.ValidationError({"status": "Suspended listings cannot be reactivated or changed by the vendor."})

        target_status = status or (instance.status if instance else "DRAFT")
        
        if target_status == "ACTIVE":
            if product:
                has_variants = product.variants.exists()
                if not has_variants:
                    p_val = price if "price" in attrs else (instance.price if instance else None)
                    s_val = stock if "stock" in attrs else (instance.stock if instance else None)
                    if p_val is None or p_val <= 0:
                        raise serializers.ValidationError({"price": "Price must be greater than zero for active listings."})
                    if s_val is None or s_val < 0:
                        raise serializers.ValidationError({"stock": "Stock must be greater than or equal to zero for active listings."})
                else:
                    p_val = price if "price" in attrs else (instance.price if instance else None)
                    s_val = stock if "stock" in attrs else (instance.stock if instance else None)
                    if p_val is not None or s_val is not None:
                        raise serializers.ValidationError("Price and stock must be set at the variant level, not the product level.")

                    variants_data = self.initial_data.get("vendor_variants", None)
                    if variants_data is None:
                        if instance:
                            existing_active = instance.vendor_variants.filter(is_active=True)
                            if not existing_active.exists():
                                raise serializers.ValidationError("At least one active variant offer is required to activate the listing.")
                            for v in existing_active:
                                if v.price <= 0 or v.stock < 0:
                                    raise serializers.ValidationError(f"Active variant '{v.product_variant.name}' must have price > 0 and stock >= 0.")
                        else:
                            raise serializers.ValidationError("At least one active variant offer is required to activate the listing.")
                    else:
                        if not isinstance(variants_data, list) or len(variants_data) == 0:
                            raise serializers.ValidationError("At least one variant offer is required to activate the listing.")
                        active_count = 0
                        for vd in variants_data:
                            is_act = vd.get("is_active", True)
                            if is_act:
                                active_count += 1
                                v_price = vd.get("price")
                                v_stock = vd.get("stock")
                                try:
                                    from decimal import Decimal
                                    if v_price is None or Decimal(str(v_price)) <= 0:
                                        raise serializers.ValidationError({"price": "Price must be greater than zero for active variants."})
                                    if v_stock is None or int(v_stock) < 0:
                                        raise serializers.ValidationError({"stock": "Stock must be greater than or equal to zero for active variants."})
                                except (ValueError, TypeError, ValidationError):
                                    raise serializers.ValidationError("Active variants require valid price > 0 and stock >= 0.")
                        if active_count == 0:
                            raise serializers.ValidationError("At least one variant must be active to set listing status to ACTIVE.")
        return attrs

    def create(self, validated_data):
        from django.db import transaction
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .services import record_inventory_movement

        vendor_variants_data = validated_data.pop("vendor_variants", [])
        
        request = self.context.get("request")
        vendor = request.user.vendor_profile
        validated_data["vendor"] = vendor

        initial_simple_stock = validated_data.get("stock", 0) or 0
        product = validated_data.get("product")
        if product and not product.variants.exists():
            validated_data["stock"] = 0

        with transaction.atomic():
            vendor_product = VendorProduct.objects.create(**validated_data)
            
            # Simple product initial stock ledger recording
            if not vendor_product.product.variants.exists() and initial_simple_stock > 0:
                record_inventory_movement(
                    user=request.user,
                    vendor_product_id=vendor_product.id,
                    vendor_product_variant_id=None,
                    movement_type="IN",
                    quantity=initial_simple_stock,
                    reason="INITIAL_STOCK",
                    note="Initial stock on product listing creation"
                )

            # Variant product initial stock ledger recording
            if vendor_variants_data:
                for variant_data in vendor_variants_data:
                    p_var = variant_data.get("product_variant")
                    req_stock = variant_data.get("stock", 0) or 0
                    
                    vpv = VendorProductVariant(
                        vendor_product=vendor_product,
                        product_variant=p_var,
                        price=variant_data.get("price"),
                        stock=0,
                        vendor_sku=variant_data.get("vendor_sku", ""),
                        is_active=variant_data.get("is_active", True)
                    )
                    try:
                        vpv.full_clean()
                    except DjangoValidationError as e:
                        raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else str(e))
                    vpv.save()

                    if req_stock > 0:
                        record_inventory_movement(
                            user=request.user,
                            vendor_product_id=vendor_product.id,
                            vendor_product_variant_id=vpv.id,
                            movement_type="IN",
                            quantity=req_stock,
                            reason="INITIAL_STOCK",
                            note="Initial stock on variant creation"
                        )
                    
            return vendor_product

    def update(self, instance, validated_data):
        from django.db import transaction
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .services import record_inventory_movement

        vendor_variants_data = validated_data.pop("vendor_variants", None)
        request = self.context.get("request")

        requested_simple_stock = validated_data.pop("stock", None)

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            # Simple product stock update via ledger
            if not instance.product.variants.exists() and requested_simple_stock is not None:
                old_stock = instance.stock or 0
                delta = requested_simple_stock - old_stock
                if delta != 0:
                    record_inventory_movement(
                        user=request.user,
                        vendor_product_id=instance.id,
                        vendor_product_variant_id=None,
                        movement_type="ADJUSTMENT",
                        quantity=delta,
                        reason="STOCK_COUNT_CORRECTION",
                        note="Stock count adjustment via product management"
                    )

            if vendor_variants_data is not None:
                for variant_data in vendor_variants_data:
                    p_var = variant_data.get("product_variant")
                    if not p_var:
                        continue
                    
                    vpv = instance.vendor_variants.filter(product_variant=p_var).first()
                    req_stock = variant_data.get("stock")

                    if vpv:
                        if "price" in variant_data:
                            vpv.price = variant_data["price"]
                        if "vendor_sku" in variant_data:
                            vpv.vendor_sku = variant_data["vendor_sku"]
                        if "is_active" in variant_data:
                            vpv.is_active = variant_data["is_active"]
                        try:
                            vpv.full_clean()
                        except DjangoValidationError as e:
                            raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else str(e))
                        vpv.save()

                        if req_stock is not None:
                            old_stock = vpv.stock or 0
                            delta = req_stock - old_stock
                            if delta != 0:
                                record_inventory_movement(
                                    user=request.user,
                                    vendor_product_id=instance.id,
                                    vendor_product_variant_id=vpv.id,
                                    movement_type="ADJUSTMENT",
                                    quantity=delta,
                                    reason="STOCK_COUNT_CORRECTION",
                                    note="Stock count adjustment via variant update"
                                )
                    else:
                        req_stock_val = req_stock if req_stock is not None else 0
                        vpv = VendorProductVariant(
                            vendor_product=instance,
                            product_variant=p_var,
                            price=variant_data.get("price"),
                            stock=0,
                            vendor_sku=variant_data.get("vendor_sku", ""),
                            is_active=variant_data.get("is_active", True)
                        )
                        try:
                            vpv.full_clean()
                        except DjangoValidationError as e:
                            raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else str(e))
                        vpv.save()

                        if req_stock_val > 0:
                            record_inventory_movement(
                                user=request.user,
                                vendor_product_id=instance.id,
                                vendor_product_variant_id=vpv.id,
                                movement_type="IN",
                                quantity=req_stock_val,
                                reason="INITIAL_STOCK",
                                note="Initial stock on variant creation"
                            )

            return instance


from .models import VendorInventoryMovement

class VendorInventoryMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorInventoryMovement
        fields = [
            "id", "vendor", "vendor_product", "vendor_product_variant",
            "product_name_snapshot", "variant_name_snapshot", "movement_type",
            "quantity", "previous_stock", "new_stock", "reason", "note",
            "reference_type", "reference_id", "created_at"
        ]
        read_only_fields = [
            "id", "vendor", "vendor_product", "vendor_product_variant",
            "product_name_snapshot", "variant_name_snapshot", "previous_stock",
            "new_stock", "created_at"
        ]

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError("Quantity cannot be zero.")
        return value

    def validate(self, attrs):
        movement_type = attrs.get("movement_type")
        quantity = attrs.get("quantity")
        reason = attrs.get("reason")

        if movement_type in ["IN", "OUT"] and quantity <= 0:
            raise serializers.ValidationError({"quantity": "Quantity must be positive for IN/OUT movements."})

        # Pairings validation
        allowed_reasons = {
            "IN": ["RESTOCK", "INITIAL_STOCK", "CUSTOMER_RETURN", "OTHER"],
            "OUT": ["DAMAGED", "LOST", "OTHER"],
            "ADJUSTMENT": ["STOCK_COUNT_CORRECTION", "MANUAL_ADJUSTMENT", "OTHER"]
        }
        if movement_type and reason and reason not in allowed_reasons.get(movement_type, []):
            raise serializers.ValidationError({"reason": f"Invalid reason '{reason}' for movement type '{movement_type}'."})

        return attrs


class VendorInventorySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    category_name = serializers.CharField(source="product.category.name", read_only=True)
    has_variants = serializers.SerializerMethodField()
    vendor_variants = VendorProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = VendorProduct
        fields = [
            "id", "product", "product_name", "category_name", "status",
            "stock", "has_variants", "vendor_variants", "created_at", "updated_at"
        ]
        read_only_fields = fields

    def get_has_variants(self, obj):
        return obj.product.variants.exists()



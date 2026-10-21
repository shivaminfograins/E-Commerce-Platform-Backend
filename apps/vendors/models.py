from django.db import models
from django.conf import settings

class Vendor(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("SUSPENDED", "Suspended"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vendor_profile"
    )
    business_name = models.CharField(max_length=255)
    store_name = models.CharField(max_length=255)
    business_email = models.EmailField(unique=True)
    business_phone = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING"
    )
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.business_name} ({self.status})"


class VendorAddress(models.Model):
    ADDRESS_TYPE_CHOICES = (
        ("BUSINESS", "Business"),
        ("WAREHOUSE", "Warehouse"),
        ("RETURN", "Return"),
    )

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="addresses"
    )
    address_type = models.CharField(
        max_length=20,
        choices=ADDRESS_TYPE_CHOICES,
        default="BUSINESS"
    )
    full_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default="India")
    postal_code = models.CharField(max_length=20)
    shiprocket_pickup_location_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Nickname of the pickup location registered in Shiprocket dashboard."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Address"
        verbose_name_plural = "Vendor Addresses"

    def __str__(self):
        return f"{self.vendor.business_name} - {self.address_type} ({self.city})"


class VendorDocument(models.Model):
    DOCUMENT_TYPE_CHOICES = (
        ("GSTIN", "GSTIN"),
        ("PAN", "PAN"),
        ("BUSINESS_REGISTRATION", "Business Registration"),
    )

    VERIFICATION_STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    )

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="documents"
    )
    document_type = models.CharField(
        max_length=50,
        choices=DOCUMENT_TYPE_CHOICES
    )
    document_file = models.FileField(upload_to="vendor_documents/")
    document_number = models.CharField(max_length=100, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_STATUS_CHOICES,
        default="PENDING"
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Document"
        verbose_name_plural = "Vendor Documents"

    def __str__(self):
        return f"{self.vendor.business_name} - {self.document_type} ({self.verification_status})"


class VendorBankAccount(models.Model):
    vendor = models.OneToOneField(
        Vendor,
        on_delete=models.CASCADE,
        related_name="bank_account"
    )
    account_holder_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=255)
    branch_name = models.CharField(max_length=255)
    ifsc_code = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Bank Account"
        verbose_name_plural = "Vendor Bank Accounts"

    def __str__(self):
        return f"{self.vendor.business_name} - Bank Account"


class VendorProduct(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("ACTIVE", "Active"),
        ("INACTIVE", "Inactive"),
        ("SUSPENDED", "Suspended"),
    )

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="vendor_products"
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="vendor_listings"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="DRAFT"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    stock = models.PositiveIntegerField(
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Product"
        verbose_name_plural = "Vendor Products"
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "product"],
                name="unique_vendor_product"
            )
        ]

    def __str__(self):
        return f"{self.vendor.business_name} - {self.product.name} ({self.status})"

    @property
    def is_effectively_active(self):
        return self.status == "ACTIVE" and self.product.is_active

    @property
    def effective_status(self):
        if self.status == "SUSPENDED":
            return "SUSPENDED"
        if not self.product.is_active:
            return "FROZEN"
        if self.status != "ACTIVE":
            return self.status
        if self.vendor_variants.exists():
            active_vars = self.vendor_variants.filter(is_active=True)
            if not active_vars.exists():
                return "INACTIVE"
            if any(v.stock > 0 for v in active_vars):
                return "AVAILABLE"
            return "OUT_OF_STOCK"
        else:
            if (self.stock or 0) > 0:
                return "AVAILABLE"
            return "OUT_OF_STOCK" 

    def delete(self, *args, **kwargs):
        if self.inventory_movements.exists():
            from django.core.exceptions import ValidationError
            raise ValidationError("Cannot delete listing with active inventory history. Set status to INACTIVE instead.")
        super().delete(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.product_id:
            # Check if the master product has any variants in the database
            has_variants = self.product.variants.exists()
            if has_variants:
                if self.price is not None or self.stock is not None:
                    raise ValidationError(
                        "For products with variants, price and stock must be set on the variant level (VendorProductVariant), not on the product level (VendorProduct)."
                    )
            else:
                if self.status == "ACTIVE":
                    if self.price is None:
                        raise ValidationError(
                            {"price": "Price is required for active products without variants."}
                        )
                    if self.stock is None:
                        raise ValidationError(
                            {"stock": "Stock is required for active products without variants."}
                        )


class VendorProductVariant(models.Model):
    vendor_product = models.ForeignKey(
        VendorProduct,
        on_delete=models.CASCADE,
        related_name="vendor_variants"
    )
    product_variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vendor_offers"
    )
    variant_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="e.g. Black / 128GB or Red / XL"
    )
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Custom key-value attributes e.g. {'Color': 'Red', 'Size': 'XL'}"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    stock = models.PositiveIntegerField(
        default=0
    )
    vendor_sku = models.CharField(
        max_length=100,
        blank=True
    )
    is_active = models.BooleanField(
        default=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Product Variant"
        verbose_name_plural = "Vendor Product Variants"
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_product", "product_variant"],
                condition=models.Q(product_variant__isnull=False),
                name="unique_vendor_product_master_variant"
            ),
            models.UniqueConstraint(
                fields=["vendor_product", "variant_name"],
                condition=models.Q(variant_name__isnull=False) & ~models.Q(variant_name=""),
                name="unique_vendor_product_custom_variant_name"
            ),
        ]

    @property
    def is_effectively_active(self):
        return (
            self.is_active and
            self.vendor_product.status == "ACTIVE" and
            self.vendor_product.product.is_active
        )

    @property
    def effective_status(self):
        if self.vendor_product.status == "SUSPENDED":
            return "SUSPENDED"
        if not self.vendor_product.product.is_active:
            return "FROZEN"
        if self.vendor_product.status != "ACTIVE":
            return self.vendor_product.status
        if not self.is_active:
            return "INACTIVE"
        if (self.stock or 0) > 0:
            return "AVAILABLE"
        return "OUT_OF_STOCK" 

    @property
    def display_name(self):
        if self.variant_name:
            return self.variant_name
        if self.product_variant:
            return self.product_variant.name
        return "Default Variant"

    def __str__(self):
        return f"{self.vendor_product.vendor.business_name} - {self.display_name} (Rs. {self.price})"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.vendor_product_id and self.product_variant_id:
            if self.product_variant.product_id != self.vendor_product.product_id:
                raise ValidationError(
                    "The selected product variant does not belong to the master product listed in VendorProduct."
                )

    def delete(self, *args, **kwargs):
        if self.inventory_movements.exists():
            from django.core.exceptions import ValidationError
            raise ValidationError("Cannot delete variant with active inventory history. Set is_active to False instead.")
        super().delete(*args, **kwargs)


class VendorInventoryMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = (
        ("IN", "Stock In"),
        ("OUT", "Stock Out"),
        ("ADJUSTMENT", "Stock Adjustment"),
    )

    REASON_CHOICES = (
        ("RESTOCK", "Restock"),
        ("INITIAL_STOCK", "Initial Stock"),
        ("CUSTOMER_RETURN", "Customer Return"),
        ("DAMAGED", "Damaged"),
        ("LOST", "Lost"),
        ("STOCK_COUNT_CORRECTION", "Stock Count Correction"),
        ("MANUAL_ADJUSTMENT", "Manual Adjustment"),
        ("OTHER", "Other"),
    )

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="inventory_movements"
    )
    vendor_product = models.ForeignKey(
        VendorProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements"
    )
    vendor_product_variant = models.ForeignKey(
        VendorProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements"
    )
    product_name_snapshot = models.CharField(max_length=255, blank=True)
    variant_name_snapshot = models.CharField(max_length=255, blank=True)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    quantity = models.IntegerField()
    previous_stock = models.PositiveIntegerField()
    new_stock = models.PositiveIntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    note = models.TextField(blank=True, default="")
    reference_type = models.CharField(max_length=50, blank=True, null=True)
    reference_id = models.CharField(max_length=100, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor Inventory Movement"
        verbose_name_plural = "Vendor Inventory Movements"
        ordering = ["-created_at"]

    def __str__(self):
        prod = self.product_name_snapshot or (self.vendor_product.product.name if self.vendor_product else "Unknown Product")
        return f"{self.vendor.business_name} - {prod} - {self.movement_type} ({self.quantity})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            from django.core.exceptions import ValidationError
            raise ValidationError("VendorInventoryMovement records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError("VendorInventoryMovement records are immutable and cannot be deleted.")


from django.db.models.signals import pre_delete
from django.dispatch import receiver

@receiver(pre_delete, sender=VendorProduct)
def protect_vendor_product_delete(sender, instance, **kwargs):
    if instance.inventory_movements.exists():
        from django.core.exceptions import ValidationError
        raise ValidationError("Cannot delete listing with active inventory history. Set status to INACTIVE instead.")

@receiver(pre_delete, sender=VendorProductVariant)
def protect_vendor_product_variant_delete(sender, instance, **kwargs):
    if instance.inventory_movements.exists():
        from django.core.exceptions import ValidationError
        raise ValidationError("Cannot delete variant with active inventory history. Set is_active to False instead.")





class VendorProductVariantImage(models.Model):
    vendor_variant = models.ForeignKey(
        VendorProductVariant,
        on_delete=models.CASCADE,
        related_name="images"
    )
    image = models.ImageField(upload_to="vendor_variants/")
    alt_text = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor Product Variant Image"
        verbose_name_plural = "Vendor Product Variant Images"
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"Image for {self.vendor_variant}"


class VendorProductRequest(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    ]

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="product_requests"
    )
    product_name = models.CharField(max_length=255)
    category = models.ForeignKey(
        "products.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_requests"
    )
    brand = models.ForeignKey(
        "products.Brand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_requests"
    )
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='vendor_product_requests/', null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING"
    )
    admin_notes = models.TextField(blank=True)
    created_master_product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_request"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vendor Product Request"
        verbose_name_plural = "Vendor Product Requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Request for {self.product_name} by {self.vendor}"


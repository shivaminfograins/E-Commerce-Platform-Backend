from django.contrib import admin
from .models import Vendor, VendorAddress, VendorDocument, VendorBankAccount

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("business_name", "store_name", "user", "status", "commission_rate", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("business_name", "store_name", "business_email", "user__email", "user__username")


@admin.register(VendorAddress)
class VendorAddressAdmin(admin.ModelAdmin):
    list_display = ("vendor", "address_type", "city", "state", "country", "postal_code")
    list_filter = ("address_type", "state")
    search_fields = ("vendor__business_name", "city", "postal_code")


@admin.register(VendorDocument)
class VendorDocumentAdmin(admin.ModelAdmin):
    list_display = ("vendor", "document_type", "verification_status", "created_at")
    list_filter = ("document_type", "verification_status")
    search_fields = ("vendor__business_name", "document_number")


@admin.register(VendorBankAccount)
class VendorBankAccountAdmin(admin.ModelAdmin):
    list_display = ("vendor", "masked_account_number", "bank_name", "ifsc_code")
    readonly_fields = ("masked_account_number", "created_at", "updated_at")

    def masked_account_number(self, obj):
        if obj and obj.account_number:
            num = obj.account_number
            if len(num) > 4:
                return "*" * (len(num) - 4) + num[-4:]
            return "****"
        return ""
    masked_account_number.short_description = "Account Number"

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        # Exclude raw account number from admin display fields
        if "account_number" in fields:
            fields = [f for f in fields if f != "account_number"]
        if "masked_account_number" not in fields:
            # Insert masked account number field
            fields.insert(1, "masked_account_number")
        return fields


from .models import VendorProduct, VendorProductVariant, VendorInventoryMovement

@admin.register(VendorProduct)
class VendorProductAdmin(admin.ModelAdmin):
    list_display = ("vendor", "product", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("vendor__business_name", "product__name")


@admin.register(VendorProductVariant)
class VendorProductVariantAdmin(admin.ModelAdmin):
    list_display = ("vendor_product", "product_variant", "price", "stock", "is_active")
    list_filter = ("is_active",)
    search_fields = ("vendor_product__vendor__business_name", "product_variant__name")


@admin.register(VendorInventoryMovement)
class VendorInventoryMovementAdmin(admin.ModelAdmin):
    list_display = (
        "vendor", "vendor_product", "vendor_product_variant",
        "movement_type", "quantity", "previous_stock", "new_stock",
        "reason", "created_by", "created_at"
    )
    list_filter = ("movement_type", "reason", "created_at")
    search_fields = (
        "vendor__business_name", "vendor_product__product__name",
        "product_name_snapshot", "variant_name_snapshot", "reference_id"
    )

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



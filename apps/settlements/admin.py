from django.contrib import admin
from .models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment


class VendorSettlementItemInline(admin.TabularInline):
    model = VendorSettlementItem
    extra = 0
    readonly_fields = [
        "vendor_order",
        "vendor_order_number_snapshot",
        "order_status_snapshot",
        "vendor_commission_rate_snapshot",
        "gross_amount",
        "discount_amount",
        "commission_amount",
        "net_payable",
        "created_at",
    ]


class VendorSettlementAdjustmentInline(admin.TabularInline):
    model = VendorSettlementAdjustment
    extra = 0
    readonly_fields = ["created_by", "created_at"]


@admin.register(VendorSettlement)
class VendorSettlementAdmin(admin.ModelAdmin):
    list_display = [
        "settlement_number",
        "vendor",
        "status",
        "payout_status",
        "period_start",
        "period_end",
        "gross_amount",
        "discount_amount",
        "commission_amount",
        "net_payable",
        "created_at",
    ]
    list_filter = ["status", "payout_status", "created_at"]
    search_fields = ["settlement_number", "vendor__business_name", "payout_reference"]
    readonly_fields = [
        "settlement_number",
        "bank_account_holder_snapshot",
        "bank_name_snapshot",
        "bank_account_number_masked_snapshot",
        "ifsc_code_snapshot",
        "approved_by",
        "approved_at",
        "settled_at",
        "created_at",
        "updated_at",
    ]
    inlines = [VendorSettlementItemInline, VendorSettlementAdjustmentInline]

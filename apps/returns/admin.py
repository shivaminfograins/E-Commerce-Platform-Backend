from django.contrib import admin
from .models import ReturnRequest, Refund


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = [
        "return_number",
        "customer",
        "vendor",
        "quantity",
        "status",
        "inspection_condition",
        "inventory_restored",
        "requested_at",
    ]
    list_filter = ["status", "inspection_condition", "inventory_restored", "vendor"]
    search_fields = ["return_number", "customer__email", "vendor__store_name"]
    readonly_fields = ["return_number", "requested_at", "approved_at", "received_at", "inspected_at", "completed_at"]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [
        "refund_number",
        "return_request",
        "amount",
        "refund_type",
        "status",
        "processed_at",
    ]
    list_filter = ["status", "refund_type"]
    search_fields = ["refund_number", "gateway_refund_id", "payout_reference"]
    readonly_fields = ["refund_number", "created_at", "processed_at"]

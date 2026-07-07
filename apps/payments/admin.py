"""
apps/payments/admin.py
======================
"""

from django.contrib import admin
from .models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "payment_method",
        "status",
        "amount",
        "transaction_id",
        "razorpay_order_id",
        "created_at",
    )
    list_filter = ("payment_method", "status", "created_at")
    search_fields = (
        "order__order_number",
        "transaction_id",
        "razorpay_order_id",
    )
    readonly_fields = (
        "order",
        "payment_method",
        "status",
        "amount",
        "transaction_id",
        "razorpay_order_id",
        "raw_response",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

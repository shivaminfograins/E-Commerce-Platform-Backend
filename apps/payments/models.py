"""
apps/payments/models.py
========================
Production-level transaction tracking model.

Design decisions:
-----------------
* Auditing: Every payment attempt (successful or failed) is stored in the
  Transaction table along with its raw gateway JSON response payload.
* Independence: Decoupled from the gateway itself so it handles COD, Razorpay,
  Stripe, etc. under a unified schema.
* Traceability: Includes indexes on gateway reference IDs for fast lookup
  during support reconciliation queries.
"""

from decimal import Decimal
from django.db import models
from django.conf import settings
from apps.orders.models import Order


class Transaction(models.Model):
    """
    Stores gateway transaction attempts and completions.
    """
    # Gateway methods
    COD      = "cod"
    RAZORPAY = "razorpay"
    STRIPE   = "stripe"

    METHOD_CHOICES = [
        (COD,      "Cash On Delivery"),
        (RAZORPAY, "Razorpay"),
        (STRIPE,   "Stripe"),
    ]

    # Transaction statuses
    PENDING  = "pending"
    SUCCESS  = "success"
    FAILED   = "failed"
    REFUNDED = "refunded"

    STATUS_CHOICES = [
        (PENDING,  "Pending"),
        (SUCCESS,  "Success"),
        (FAILED,   "Failed"),
        (REFUNDED, "Refunded"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="transactions",
        help_text="The order associated with this transaction.",
    )

    payment_method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        default=RAZORPAY,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Transaction amount captured in standard currency units.",
    )

    # ── Gateway references ─────────────────────────────────────────────────
    transaction_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Gateway-specific payment transaction ID (e.g. pay_K1j2u3z).",
    )

    razorpay_order_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Razorpay Order ID created in pre-checkout.",
    )

    # ── Auditing and Debugging ─────────────────────────────────────────────
    raw_response = models.JSONField(
        blank=True,
        null=True,
        help_text="Full response JSON received from the payment gateway.",
    )

    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Detailed failure reason returned by the gateway.",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        indexes = [
            models.Index(fields=["razorpay_order_id"], name="idx_txn_rzp_order"),
            models.Index(fields=["transaction_id"], name="idx_txn_gateway_id"),
        ]

    def __str__(self):
        return f"{self.order.order_number} - {self.get_payment_method_display()} - {self.get_status_display()}"

from decimal import Decimal
import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


class ReturnRequest(models.Model):
    # Reasons
    DAMAGED = "DAMAGED"
    DEFECTIVE = "DEFECTIVE"
    WRONG_ITEM = "WRONG_ITEM"
    WRONG_SIZE = "WRONG_SIZE"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    CUSTOMER_CHANGED_MIND = "CUSTOMER_CHANGED_MIND"
    OTHER = "OTHER"

    REASON_CHOICES = [
        (DAMAGED, "Damaged Product"),
        (DEFECTIVE, "Defective Product"),
        (WRONG_ITEM, "Wrong Item Delivered"),
        (WRONG_SIZE, "Wrong Size / Fit"),
        (NOT_AS_DESCRIBED, "Not as Described"),
        (CUSTOMER_CHANGED_MIND, "Customer Changed Mind"),
        (OTHER, "Other"),
    ]

    # Statuses
    REQUESTED = "REQUESTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PICKUP_PENDING = "PICKUP_PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    INSPECTED = "INSPECTED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    REFUND_FAILED = "REFUND_FAILED"

    STATUS_CHOICES = [
        (REQUESTED, "Requested"),
        (UNDER_REVIEW, "Under Review"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (PICKUP_PENDING, "Pickup Pending"),
        (IN_TRANSIT, "In Transit"),
        (RECEIVED, "Received"),
        (INSPECTED, "Inspected"),
        (REFUND_PENDING, "Refund Pending"),
        (REFUNDED, "Refunded"),
        (CANCELLED, "Cancelled"),
        (REFUND_FAILED, "Refund Failed"),
    ]

    # Conditions
    NOT_INSPECTED = "NOT_INSPECTED"
    SELLABLE = "SELLABLE"
    DAMAGED_COND = "DAMAGED"
    DEFECTIVE_COND = "DEFECTIVE"
    MISSING_PARTS = "MISSING_PARTS"
    USED = "USED"
    REJECTED_COND = "REJECTED"

    CONDITION_CHOICES = [
        (NOT_INSPECTED, "Not Inspected"),
        (SELLABLE, "Sellable Condition"),
        (DAMAGED_COND, "Damaged Condition"),
        (DEFECTIVE_COND, "Defective Condition"),
        (MISSING_PARTS, "Missing Parts"),
        (USED, "Used Item"),
        (REJECTED_COND, "Rejected Condition"),
    ]

    return_number = models.CharField(max_length=50, unique=True, db_index=True, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="return_requests")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="return_requests")
    vendor_order = models.ForeignKey("orders.VendorOrder", on_delete=models.PROTECT, related_name="return_requests")
    order_item = models.ForeignKey("orders.OrderItem", on_delete=models.PROTECT, related_name="return_requests")
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="return_requests")

    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    customer_note = models.TextField(blank=True, default="")

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=REQUESTED, db_index=True)
    inspection_condition = models.CharField(max_length=30, choices=CONDITION_CHOICES, default=NOT_INSPECTED)
    inspection_note = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")

    inventory_restored = models.BooleanField(default=False, help_text="Independent inventory restoration idempotency flag")
    settlement_adjustment_applied = models.BooleanField(default=False, help_text="Flag indicating if settlement adjustment has been attached or deferred")

    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    inspected_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="check_positive_return_quantity"
            )
        ]
        indexes = [
            models.Index(fields=["vendor", "status"]),
            models.Index(fields=["customer", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.return_number:
            date_str = timezone.now().strftime("%Y%m%d")
            rand_str = uuid.uuid4().hex[:6].upper()
            self.return_number = f"RET-{date_str}-{rand_str}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.return_number} - {self.get_status_display()} (Qty: {self.quantity})"


class Refund(models.Model):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (SUCCESS, "Success"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]

    GATEWAY_REFUND = "GATEWAY_REFUND"
    MANUAL_BANK_REFUND = "MANUAL_BANK_REFUND"
    COD_CREDIT = "COD_CREDIT"

    REFUND_TYPE_CHOICES = [
        (GATEWAY_REFUND, "Gateway Refund"),
        (MANUAL_BANK_REFUND, "Manual Bank Refund"),
        (COD_CREDIT, "COD Credit"),
    ]

    refund_number = models.CharField(max_length=50, unique=True, db_index=True, editable=False)
    return_request = models.OneToOneField(ReturnRequest, on_delete=models.PROTECT, related_name="refund")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="refunds")
    vendor_order = models.ForeignKey("orders.VendorOrder", on_delete=models.PROTECT, related_name="refunds")
    payment_transaction = models.ForeignKey("payments.Transaction", on_delete=models.SET_NULL, null=True, blank=True, related_name="refunds")

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    refund_type = models.CharField(max_length=30, choices=REFUND_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING, db_index=True)

    gateway_refund_id = models.CharField(max_length=100, blank=True, default="")
    payout_reference = models.CharField(max_length=100, blank=True, default="", help_text="Bank UTR or transaction reference for manual COD refunds")
    gateway_response = models.JSONField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default="")

    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="processed_refunds")
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="check_positive_refund_amount"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.refund_number:
            date_str = timezone.now().strftime("%Y%m%d")
            rand_str = uuid.uuid4().hex[:6].upper()
            self.refund_number = f"REF-{date_str}-{rand_str}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.refund_number} - {self.amount} ({self.get_status_display()})"

import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class VendorSettlement(models.Model):
    # Settlement Lifecycle Statuses
    DRAFT            = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED         = "APPROVED"
    SETTLED          = "SETTLED"
    CANCELLED        = "CANCELLED"

    STATUS_CHOICES = [
        (DRAFT,            "Draft"),
        (PENDING_APPROVAL, "Pending Approval"),
        (APPROVED,         "Approved"),
        (SETTLED,          "Settled"),
        (CANCELLED,        "Cancelled"),
    ]

    # Payout Lifecycle Statuses (Decoupled)
    PAYOUT_UNPAID     = "UNPAID"
    PAYOUT_QUEUED     = "QUEUED"
    PAYOUT_PROCESSING = "PROCESSING"
    PAYOUT_PAID       = "PAID"
    PAYOUT_FAILED     = "FAILED"

    PAYOUT_STATUS_CHOICES = [
        (PAYOUT_UNPAID,     "Unpaid"),
        (PAYOUT_QUEUED,     "Queued"),
        (PAYOUT_PROCESSING, "Processing"),
        (PAYOUT_PAID,       "Paid"),
        (PAYOUT_FAILED,     "Failed"),
    ]

    settlement_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        editable=False
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="settlements"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=DRAFT,
        db_index=True
    )
    payout_status = models.CharField(
        max_length=20,
        choices=PAYOUT_STATUS_CHOICES,
        default=PAYOUT_UNPAID,
        db_index=True
    )

    period_start = models.DateField()
    period_end = models.DateField()

    # Financial breakdown (Strict Decimal precision)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    adjustment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    net_payable = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Bank Snapshots (Populated on approval)
    bank_account_holder_snapshot = models.CharField(max_length=255, blank=True, default="")
    bank_name_snapshot = models.CharField(max_length=255, blank=True, default="")
    bank_account_number_masked_snapshot = models.CharField(max_length=100, blank=True, default="")
    ifsc_code_snapshot = models.CharField(max_length=20, blank=True, default="")

    # Audit & Tracking
    payout_reference = models.CharField(max_length=100, blank=True, default="")
    payout_method = models.CharField(max_length=50, blank=True, default="MANUAL_BANK_TRANSFER")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_settlements"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Vendor Settlement"
        verbose_name_plural = "Vendor Settlements"
        indexes = [
            models.Index(fields=["vendor", "status"], name="idx_settlement_vendor_status"),
            models.Index(fields=["payout_status"], name="idx_settlement_payout_status"),
            models.Index(fields=["created_at"], name="idx_settlement_created_at"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(net_payable__gte=Decimal("0.00")),
                name="check_positive_net_payable"
            )
        ]

    def _generate_settlement_number(self):
        date_str = timezone.now().strftime("%Y%m%d")
        uid_chunk = uuid.uuid4().hex[:6].upper()
        return f"STL-{date_str}-{self.vendor_id}-{uid_chunk}"

    def clean(self):
        super().clean()
        if self.net_payable < Decimal("0.00"):
            raise ValidationError({"net_payable": "Net payable cannot be negative."})

    def save(self, *args, **kwargs):
        if not self.settlement_number:
            self.settlement_number = self._generate_settlement_number()

        self.full_clean()

        if self.pk is not None:
            old_inst = VendorSettlement.objects.get(pk=self.pk)
            locked_statuses = [self.PENDING_APPROVAL, self.APPROVED, self.SETTLED]

            # 1. Protect Settled status from any regression or illegal transition
            if old_inst.status == self.SETTLED and self.status != self.SETTLED:
                raise ValidationError("A settled settlement status cannot be altered.")
            
            if old_inst.payout_status == self.PAYOUT_PAID and self.payout_status != self.PAYOUT_PAID:
                raise ValidationError("A settled payout status cannot be reverted.")

            # 2. Strict status transition rules
            allowed_transitions = {
                self.DRAFT: [self.DRAFT, self.PENDING_APPROVAL, self.CANCELLED],
                self.PENDING_APPROVAL: [self.PENDING_APPROVAL, self.APPROVED, self.CANCELLED],
                self.APPROVED: [self.APPROVED, self.SETTLED, self.CANCELLED],
                self.SETTLED: [self.SETTLED],
                self.CANCELLED: [self.CANCELLED],
            }
            if self.status not in allowed_transitions.get(old_inst.status, []):
                raise ValidationError(f"Transition from status '{old_inst.status}' to '{self.status}' is illegal.")

            # 3. Payout status transition rules
            if self.status == self.SETTLED and self.payout_status != self.PAYOUT_PAID:
                raise ValidationError("A settlement cannot be marked SETTLED unless payout_status is PAID.")

            # 4. Freeze financial totals from PENDING_APPROVAL
            if old_inst.status in locked_statuses:
                financial_fields = [
                    "gross_amount", "discount_amount", "tax_amount",
                    "commission_amount", "adjustment_amount", "net_payable",
                    "period_start", "period_end", "vendor_id"
                ]
                for fld in financial_fields:
                    if getattr(self, fld) != getattr(old_inst, fld):
                        raise ValidationError(f"Financial field '{fld}' is frozen and cannot be modified after PENDING_APPROVAL.")

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status in [self.PENDING_APPROVAL, self.APPROVED, self.SETTLED]:
            raise ValidationError("Settlement records cannot be deleted once submitted or approved.")
        super().delete(*args, **kwargs)

    def recalculate_totals(self):
        """
        Recalculates financial totals from constituent VendorSettlementItems and VendorSettlementAdjustments.
        Allowed only in DRAFT status.
        """
        if self.status != self.DRAFT:
            raise ValidationError("Financial totals can only be recalculated while in DRAFT status.")

        items = list(self.items.all())
        adjustments = list(self.adjustments.all())

        self.gross_amount = sum((it.gross_amount for it in items), Decimal("0.00"))
        self.discount_amount = sum((it.discount_amount for it in items), Decimal("0.00"))
        self.commission_amount = sum((it.commission_amount for it in items), Decimal("0.00"))

        adj_total = Decimal("0.00")
        for adj in adjustments:
            if adj.adjustment_type == "CREDIT":
                adj_total += adj.amount
            elif adj.adjustment_type == "DEBIT":
                adj_total -= adj.amount
        self.adjustment_amount = adj_total

        calc_net = self.gross_amount - self.discount_amount - self.commission_amount + self.adjustment_amount
        if calc_net < Decimal("0.00"):
            raise ValidationError("Calculated net payable is negative. Check applied adjustments.")

        self.net_payable = calc_net

    def __str__(self):
        return f"{self.settlement_number} — {self.vendor.business_name} ({self.get_status_display()})"


class VendorSettlementItem(models.Model):
    settlement = models.ForeignKey(
        VendorSettlement,
        on_delete=models.CASCADE,
        related_name="items"
    )
    vendor_order = models.OneToOneField(
        "orders.VendorOrder",
        on_delete=models.PROTECT,
        related_name="settlement_item"
    )
    
    vendor_order_number_snapshot = models.CharField(max_length=50)
    order_status_snapshot = models.CharField(max_length=20)
    vendor_commission_rate_snapshot = models.DecimalField(max_digits=5, decimal_places=2)

    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    net_payable = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor Settlement Item"
        verbose_name_plural = "Vendor Settlement Items"
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_order"],
                name="unique_vendor_order_settlement_item"
            )
        ]

    def clean(self):
        super().clean()
        if self.settlement_id:
            if self.settlement.status != VendorSettlement.DRAFT:
                raise ValidationError("Settlement items cannot be added or modified unless the settlement is in DRAFT status.")

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk is not None:
            if self.settlement.status != VendorSettlement.DRAFT:
                raise ValidationError("Settlement items cannot be edited after PENDING_APPROVAL.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.settlement.status != VendorSettlement.DRAFT:
            raise ValidationError("Settlement items cannot be deleted after PENDING_APPROVAL.")
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.settlement.settlement_number} item: {self.vendor_order_number_snapshot}"


class VendorSettlementAdjustment(models.Model):
    ADJUSTMENT_TYPES = [
        ("CREDIT", "Credit"),
        ("DEBIT", "Debit"),
    ]

    settlement = models.ForeignKey(
        VendorSettlement,
        on_delete=models.CASCADE,
        related_name="adjustments"
    )
    adjustment_type = models.CharField(max_length=10, choices=ADJUSTMENT_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_settlement_adjustments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor Settlement Adjustment"
        verbose_name_plural = "Vendor Settlement Adjustments"

    def clean(self):
        super().clean()
        if self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Adjustment amount must be strictly greater than 0.00."})
        if self.settlement_id:
            if self.settlement.status != VendorSettlement.DRAFT:
                raise ValidationError("Adjustments can only be added while the settlement is in DRAFT status.")

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk is not None:
            if self.settlement.status != VendorSettlement.DRAFT:
                raise ValidationError("Adjustments cannot be modified after PENDING_APPROVAL.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.settlement.status != VendorSettlement.DRAFT:
            raise ValidationError("Adjustments cannot be deleted after PENDING_APPROVAL.")
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.settlement.settlement_number} adj ({self.adjustment_type}): {self.amount}"


class UnappliedRefundAdjustment(models.Model):
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="unapplied_refund_adjustments"
    )
    return_request = models.ForeignKey(
        "returns.ReturnRequest",
        on_delete=models.PROTECT,
        related_name="unapplied_refund_adjustments"
    )
    vendor_order = models.ForeignKey(
        "orders.VendorOrder",
        on_delete=models.PROTECT,
        related_name="unapplied_refund_adjustments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    adjustment_type = models.CharField(max_length=10, default="DEBIT")
    reason = models.TextField()
    is_applied = models.BooleanField(default=False, db_index=True)
    applied_settlement = models.ForeignKey(
        VendorSettlement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_deferred_adjustments"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Unapplied Refund Adjustment"
        verbose_name_plural = "Unapplied Refund Adjustments"

    def __str__(self):
        status_str = f"Applied to {self.applied_settlement.settlement_number}" if self.is_applied and self.applied_settlement else "Unapplied"
        return f"{self.vendor.business_name} deferred adj ({self.adjustment_type}): {self.amount} [{status_str}]"


from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment
from apps.orders.models import Order, VendorOrder


def generate_vendor_settlements(vendor, period_start, period_end, user):
    """
    Generates a draft VendorSettlement for all eligible delivered sub-orders belonging to the vendor.
    Guarantees strict concurrency protection and idempotency via SELECT FOR UPDATE and database constraints.
    """
    if vendor.status != "APPROVED":
        raise ValidationError("Only APPROVED vendors are eligible for settlement generation.")

    with transaction.atomic():
        # Lock eligible vendor orders for update
        eligible_orders = list(
            VendorOrder.objects.select_for_update()
            .filter(
                vendor=vendor,
                status=VendorOrder.DELIVERED,
                master_order__payment_status=Order.PAYMENT_PAID,
                created_at__date__gte=period_start,
                created_at__date__lte=period_end,
                settlement_item__isnull=True
            )
            .select_related("master_order")
        )

        if not eligible_orders:
            raise ValidationError("No eligible delivered and paid sub-orders found for the specified period.")

        # Create draft settlement shell
        settlement = VendorSettlement.objects.create(
            vendor=vendor,
            status=VendorSettlement.DRAFT,
            payout_status=VendorSettlement.PAYOUT_UNPAID,
            period_start=period_start,
            period_end=period_end,
            notes=f"Auto-generated settlement statement for {vendor.business_name} ({period_start} to {period_end})"
        )

        comm_rate = vendor.commission_rate
        items_to_create = []

        for v_order in eligible_orders:
            gross = v_order.subtotal
            discount = v_order.discount
            funding_type = v_order.discount_funding_type

            if funding_type == "VENDOR_FUNDED":
                vend_discount = v_order.vendor_discount_amount or discount
                plat_discount = Decimal("0.00")
                comm_base = gross
                comm_amount = (comm_base * (comm_rate / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                net_item = gross - vend_discount - comm_amount
            else:
                plat_discount = v_order.platform_discount_amount or discount
                vend_discount = Decimal("0.00")
                comm_base = max(Decimal("0.00"), gross - plat_discount)
                comm_amount = (comm_base * (comm_rate / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                net_item = comm_base - comm_amount

            item = VendorSettlementItem(
                settlement=settlement,
                vendor_order=v_order,
                vendor_order_number_snapshot=v_order.vendor_order_number,
                order_status_snapshot=v_order.status,
                vendor_commission_rate_snapshot=comm_rate,
                gross_amount=gross,
                discount_amount=discount,
                platform_discount_amount=plat_discount,
                vendor_discount_amount=vend_discount,
                commission_amount=comm_amount,
                net_payable=net_item
            )
            items_to_create.append(item)

        VendorSettlementItem.objects.bulk_create(items_to_create)

        # Discover and attach any unapplied deferred refund adjustments for this vendor
        from .models import UnappliedRefundAdjustment
        unapplied_adjs = list(
            UnappliedRefundAdjustment.objects.select_for_update().filter(
                vendor=vendor,
                is_applied=False
            )
        )
        for unapplied in unapplied_adjs:
            add_settlement_adjustment(
                settlement=settlement,
                adjustment_type=unapplied.adjustment_type,
                amount=unapplied.amount,
                reason=unapplied.reason,
                user=user
            )
            unapplied.is_applied = True
            unapplied.applied_settlement = settlement
            unapplied.applied_at = timezone.now()
            unapplied.save(update_fields=["is_applied", "applied_settlement", "applied_at"])

        # Recalculate totals from items and adjustments and save
        settlement.recalculate_totals()
        settlement.save()

        return settlement


def submit_settlement_for_approval(settlement, user):
    """
    Transitions settlement from DRAFT to PENDING_APPROVAL. Freezes financial figures.
    """
    with transaction.atomic():
        settlement = VendorSettlement.objects.select_for_update().get(pk=settlement.pk)

        if settlement.status != VendorSettlement.DRAFT:
            raise ValidationError(f"Only DRAFT settlements can be submitted. Current status: '{settlement.status}'.")

        if not settlement.items.exists():
            raise ValidationError("Cannot submit an empty settlement with no line items.")

        settlement.recalculate_totals()
        settlement.status = VendorSettlement.PENDING_APPROVAL
        settlement.save()
        return settlement


def approve_settlement(settlement, user):
    """
    Transitions settlement from PENDING_APPROVAL to APPROVED and snapshots bank details.
    """
    with transaction.atomic():
        settlement = VendorSettlement.objects.select_for_update().get(pk=settlement.pk)

        if settlement.status != VendorSettlement.PENDING_APPROVAL:
            raise ValidationError(f"Only PENDING_APPROVAL settlements can be approved. Current status: '{settlement.status}'.")

        vendor = settlement.vendor
        if not hasattr(vendor, "bank_account"):
            raise ValidationError(f"Vendor '{vendor.business_name}' does not have a registered bank account.")

        bank = vendor.bank_account
        raw_num = bank.account_number or ""
        masked_num = f"XXXX-XXXX-{raw_num[-4:]}" if len(raw_num) >= 4 else "XXXX"

        settlement.bank_account_holder_snapshot = bank.account_holder_name
        settlement.bank_name_snapshot = bank.bank_name
        settlement.bank_account_number_masked_snapshot = masked_num
        settlement.ifsc_code_snapshot = bank.ifsc_code

        settlement.status = VendorSettlement.APPROVED
        settlement.approved_by = user
        settlement.approved_at = timezone.now()
        settlement.save()

        return settlement


def process_settlement_payout(settlement, payout_reference, payout_method, user):
    """
    Marks settlement payout as PAID and transitions status to SETTLED.
    """
    if not payout_reference or not payout_reference.strip():
        raise ValidationError({"payout_reference": "Payout reference transaction/UTR code is required."})

    with transaction.atomic():
        settlement = VendorSettlement.objects.select_for_update().get(pk=settlement.pk)

        if settlement.status != VendorSettlement.APPROVED:
            raise ValidationError(f"Only APPROVED settlements can be processed for payout. Current status: '{settlement.status}'.")

        if settlement.payout_status == VendorSettlement.PAYOUT_PAID and settlement.status == VendorSettlement.SETTLED:
            raise ValidationError("Payout is already settled and marked PAID. Cannot re-process.")

        settlement.payout_reference = payout_reference.strip()
        if payout_method:
            settlement.payout_method = payout_method.strip()

        settlement.payout_status = VendorSettlement.PAYOUT_PAID
        settlement.status = VendorSettlement.SETTLED
        settlement.settled_at = timezone.now()
        settlement.save()

        return settlement


def add_settlement_adjustment(settlement, adjustment_type, amount, reason, user):
    """
    Adds a manual credit or debit adjustment to a draft settlement.
    """
    if adjustment_type not in ["CREDIT", "DEBIT"]:
        raise ValidationError({"adjustment_type": "Invalid adjustment type. Must be 'CREDIT' or 'DEBIT'."})

    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": "Adjustment amount must be positive."})

    if not reason or not reason.strip():
        raise ValidationError({"reason": "A clear explanation reason is required for adjustments."})

    with transaction.atomic():
        settlement = VendorSettlement.objects.select_for_update().get(pk=settlement.pk)

        if settlement.status != VendorSettlement.DRAFT:
            raise ValidationError("Adjustments can only be added while settlement is in DRAFT status.")

        adj = VendorSettlementAdjustment.objects.create(
            settlement=settlement,
            adjustment_type=adjustment_type,
            amount=amount,
            reason=reason.strip(),
            created_by=user
        )

        settlement.recalculate_totals()
        settlement.save()

        return adj

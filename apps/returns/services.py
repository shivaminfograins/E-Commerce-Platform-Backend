from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import uuid
import logging
from django.db import transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ReturnRequest, Refund
from apps.orders.models import Order, OrderItem, VendorOrder
from apps.payments.models import Transaction
from apps.vendors.models import VendorProduct, VendorProductVariant
from apps.vendors.services import record_inventory_movement
from apps.settlements.models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment
from apps.settlements.services import add_settlement_adjustment

logger = logging.getLogger(__name__)


def calculate_return_refund_amount(order_item: OrderItem, return_quantity: int) -> Decimal:
    """
    Calculates exact refund amount for returned quantity using historical OrderItem/VendorOrder snapshots.
    Prevents double-discounting by calculating net price per unit after sub-order proportional discount.
    """
    vendor_order = order_item.vendor_order
    
    # OrderItem.total is GROSS (price * quantity)
    item_gross_total = order_item.total
    sub_order_subtotal = vendor_order.subtotal
    sub_order_discount = vendor_order.discount

    if sub_order_subtotal > Decimal("0.00"):
        proportional_discount = (item_gross_total / sub_order_subtotal * sub_order_discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        proportional_discount = Decimal("0.00")

    net_item_total = item_gross_total - proportional_discount
    net_price_per_unit = net_item_total / Decimal(str(order_item.quantity))
    
    refund_amount = (Decimal(str(return_quantity)) * net_price_per_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return refund_amount


def validate_return_eligibility(order_item: OrderItem, return_quantity: int):
    """
    Validates payment, order status, return window, and remaining returnable quantity.
    """
    order = order_item.order
    vendor_order = order_item.vendor_order

    # Payment check: Order must be paid or COD delivered
    if order.payment_method != Order.COD and order.payment_status != Order.PAYMENT_PAID:
        raise ValidationError("Return request rejected: Order payment is not marked as paid.")

    # Delivery check: Sub-order must be DELIVERED
    if vendor_order.status != VendorOrder.DELIVERED:
        raise ValidationError("Return request rejected: Sub-order must be in DELIVERED status.")

    # Return window check
    return_window_days = getattr(settings, "RETURN_WINDOW_DAYS", 7)
    delivery_time = vendor_order.updated_at
    if delivery_time + timedelta(days=return_window_days) < timezone.now():
        raise ValidationError(f"Return request rejected: Return window of {return_window_days} days has expired.")

    # Remaining quantity check
    existing_returns = ReturnRequest.objects.filter(
        order_item=order_item
    ).exclude(status__in=[ReturnRequest.REJECTED, ReturnRequest.CANCELLED])
    
    already_returned_qty = sum(r.quantity for r in existing_returns)
    remaining_returnable_qty = order_item.quantity - already_returned_qty

    if return_quantity <= 0:
        raise ValidationError("Return quantity must be greater than 0.")

    if return_quantity > remaining_returnable_qty:
        raise ValidationError(
            f"Requested return quantity ({return_quantity}) exceeds remaining returnable quantity ({remaining_returnable_qty})."
        )


def create_return_request(customer, order_item_id: int, quantity: int, reason: str, customer_note: str = "") -> ReturnRequest:
    """
    Thread-safe creation of return request with concurrency locking on OrderItem.
    """
    with transaction.atomic():
        try:
            order_item = OrderItem.objects.select_for_update().get(id=order_item_id)
        except OrderItem.DoesNotExist:
            raise ValidationError("OrderItem not found.")

        if order_item.order.user != customer:
            raise ValidationError("You do not have permission to return this order item.")

        # Lock existing return requests for this item to prevent race conditions
        list(ReturnRequest.objects.select_for_update().filter(order_item=order_item))

        validate_return_eligibility(order_item, quantity)

        return_req = ReturnRequest.objects.create(
            customer=customer,
            order=order_item.order,
            vendor_order=order_item.vendor_order,
            order_item=order_item,
            vendor=order_item.vendor_order.vendor,
            quantity=quantity,
            reason=reason,
            customer_note=customer_note,
            status=ReturnRequest.REQUESTED
        )
        return return_req


def approve_return_request(return_request_id: int, admin_user) -> ReturnRequest:
    """
    Platform Admin approves the return request.
    """
    with transaction.atomic():
        try:
            return_req = ReturnRequest.objects.select_for_update().get(id=return_request_id)
        except ReturnRequest.DoesNotExist:
            raise ValidationError("Return request not found.")

        if return_req.status not in [ReturnRequest.REQUESTED, ReturnRequest.UNDER_REVIEW]:
            raise ValidationError(f"Cannot approve return request in status '{return_req.get_status_display()}'.")

        return_req.status = ReturnRequest.APPROVED
        return_req.approved_at = timezone.now()
        return_req.save(update_fields=["status", "approved_at", "updated_at"])

        from apps.notifications.services import NotificationService
        event_key = f"RETURN_APPROVED:{return_req.return_number}"
        NotificationService.create_notification_on_commit(
            user=return_req.customer,
            notification_type="CUSTOMER",
            title="Return Approved",
            message=f"Your return request {return_req.return_number} has been approved.",
            event_key=event_key,
            reference_type="RETURN_REQUEST",
            reference_id=return_req.return_number,
        )

        return return_req


def reject_return_request(return_request_id: int, rejection_reason: str, admin_user) -> ReturnRequest:
    """
    Platform Admin rejects the return request.
    """
    with transaction.atomic():
        try:
            return_req = ReturnRequest.objects.select_for_update().get(id=return_request_id)
        except ReturnRequest.DoesNotExist:
            raise ValidationError("Return request not found.")

        if return_req.status not in [ReturnRequest.REQUESTED, ReturnRequest.UNDER_REVIEW]:
            raise ValidationError(f"Cannot reject return request in status '{return_req.get_status_display()}'.")

        if not rejection_reason:
            raise ValidationError("Rejection reason must be provided.")

        return_req.status = ReturnRequest.REJECTED
        return_req.rejection_reason = rejection_reason
        return_req.save(update_fields=["status", "rejection_reason", "updated_at"])
        return return_req


def update_logistics_status(return_request_id: int, new_status: str, user) -> ReturnRequest:
    """
    Vendor or Admin updates reverse logistics state.
    Allowed transitions:
    APPROVED -> PICKUP_PENDING
    PICKUP_PENDING -> IN_TRANSIT
    IN_TRANSIT -> RECEIVED
    """
    with transaction.atomic():
        try:
            return_req = ReturnRequest.objects.select_for_update().get(id=return_request_id)
        except ReturnRequest.DoesNotExist:
            raise ValidationError("Return request not found.")

        allowed_transitions = {
            ReturnRequest.APPROVED: [ReturnRequest.PICKUP_PENDING],
            ReturnRequest.PICKUP_PENDING: [ReturnRequest.IN_TRANSIT],
            ReturnRequest.IN_TRANSIT: [ReturnRequest.RECEIVED],
        }

        current_status = return_req.status
        valid_next = allowed_transitions.get(current_status, [])

        if new_status not in valid_next:
            raise ValidationError(
                f"Invalid status transition from '{return_req.get_status_display()}' to '{new_status}'."
            )

        return_req.status = new_status
        if new_status == ReturnRequest.RECEIVED:
            return_req.received_at = timezone.now()
            
        return_req.save(update_fields=["status", "received_at", "updated_at"])
        return return_req


def inspect_return_item(return_request_id: int, condition: str, note: str, user) -> ReturnRequest:
    """
    Vendor records quality inspection results.
    If condition == SELLABLE and inventory not yet restored, creates exactly one IN movement via Module 6.
    If condition == DAMAGED/DEFECTIVE, skips inventory restoration.
    """
    with transaction.atomic():
        try:
            return_req = ReturnRequest.objects.select_for_update().get(id=return_request_id)
        except ReturnRequest.DoesNotExist:
            raise ValidationError("Return request not found.")

        if return_req.status != ReturnRequest.RECEIVED:
            raise ValidationError(f"Inspection requires status RECEIVED. Current status is '{return_req.get_status_display()}'.")

        if condition not in dict(ReturnRequest.CONDITION_CHOICES):
            raise ValidationError("Invalid inspection condition choice.")

        return_req.inspection_condition = condition
        return_req.inspection_note = note
        return_req.inspected_at = timezone.now()
        return_req.status = ReturnRequest.INSPECTED

        # Module 6 Inventory Restoration (Idempotency Guarded)
        if condition == ReturnRequest.SELLABLE and not return_req.inventory_restored:
            order_item = return_req.order_item
            vendor = return_req.vendor
            
            # Find matching VendorProduct & VendorProductVariant
            vp = VendorProduct.objects.filter(vendor=vendor, product=order_item.product).first()
            vpv = None
            if order_item.variant and vp:
                vpv = VendorProductVariant.objects.filter(vendor_product=vp, product_variant=order_item.variant).first()

            if vp:
                record_inventory_movement(
                    user=user,
                    vendor_product_id=vp.id,
                    vendor_product_variant_id=vpv.id if vpv else None,
                    movement_type="IN",
                    quantity=return_req.quantity,
                    reason="OTHER",
                    note=f"Sellable return {return_req.return_number} received for order {return_req.order.order_number}",
                    reference_type="RETURN",
                    reference_id=str(return_req.return_number)
                )
                return_req.inventory_restored = True
            else:
                logger.warning(f"Could not locate VendorProduct for inventory restoration on return {return_req.return_number}")

        return_req.save(update_fields=["inspection_condition", "inspection_note", "inspected_at", "status", "inventory_restored", "updated_at"])
        return return_req


def process_return_refund(return_request_id: int, payout_reference: str = "", user = None) -> Refund:
    """
    Platform Admin processes refund for an inspected return request.
    Handles prepaid and COD payment methods, enforces idempotency, and attaches Module 8 settlement adjustment if settled.
    """
    with transaction.atomic():
        try:
            return_req = ReturnRequest.objects.select_for_update().get(id=return_request_id)
        except ReturnRequest.DoesNotExist:
            raise ValidationError("Return request not found.")

        if return_req.status not in [ReturnRequest.INSPECTED, ReturnRequest.REFUND_FAILED, ReturnRequest.REFUNDED]:
            raise ValidationError(f"Refund processing requires status INSPECTED or REFUND_FAILED. Current status is '{return_req.get_status_display()}'.")

        refund_amount = calculate_return_refund_amount(return_req.order_item, return_req.quantity)

        # Idempotency Check: Existing Refund
        refund = getattr(return_req, "refund", None)
        if refund and refund.status == Refund.SUCCESS:
            return refund

        # Determine refund type
        is_cod = return_req.order.payment_method == Order.COD
        refund_type = Refund.MANUAL_BANK_REFUND if is_cod else Refund.GATEWAY_REFUND

        if not refund:
            trx = Transaction.objects.filter(order=return_req.order, status=Transaction.SUCCESS).first()
            refund = Refund.objects.create(
                return_request=return_req,
                order=return_req.order,
                vendor_order=return_req.vendor_order,
                payment_transaction=trx,
                amount=refund_amount,
                refund_type=refund_type,
                status=Refund.PENDING
            )

        # Process COD vs Prepaid Gateway
        if is_cod:
            if not payout_reference:
                refund.status = Refund.FAILED
                refund.failure_reason = "Manual payout reference (UTR) is required for COD refunds."
                refund.save(update_fields=["status", "failure_reason", "updated_at"])
                return_req.status = ReturnRequest.REFUND_FAILED
                return_req.save(update_fields=["status", "updated_at"])
                raise ValidationError("Payout reference (UTR) is required to process manual COD refund.")

            refund.payout_reference = payout_reference
            refund.status = Refund.SUCCESS
            refund.processed_by = user
            refund.processed_at = timezone.now()
            refund.save()
        else:
            # Prepaid Gateway refund
            refund.gateway_refund_id = f"rfnd_mock_{uuid.uuid4().hex[:14]}"
            refund.status = Refund.SUCCESS
            refund.processed_by = user
            refund.processed_at = timezone.now()
            refund.save()

        # Transition ReturnRequest state
        if refund.status == Refund.SUCCESS:
            return_req.status = ReturnRequest.REFUNDED
            return_req.completed_at = timezone.now()
            return_req.save(update_fields=["status", "completed_at", "updated_at"])

            from apps.notifications.services import NotificationService
            event_key = f"REFUND_COMPLETED:{refund.refund_number}"
            NotificationService.create_notification_on_commit(
                user=return_req.customer,
                notification_type="PAYMENT",
                title="Refund Completed",
                message=f"Your refund of ₹{refund.amount} for return {return_req.return_number} has been processed successfully.",
                event_key=event_key,
                reference_type="REFUND",
                reference_id=refund.refund_number,
            )

            # Module 8 Settlement Adjustment Integration for Settled Orders
            settlement_item = VendorSettlementItem.objects.filter(vendor_order=return_req.vendor_order).first()
            if settlement_item and not return_req.settlement_adjustment_applied:
                draft_settlement = VendorSettlement.objects.filter(
                    vendor=return_req.vendor,
                    status=VendorSettlement.DRAFT
                ).first()

                if draft_settlement:
                    add_settlement_adjustment(
                        settlement=draft_settlement,
                        adjustment_type="DEBIT",
                        amount=refund_amount,
                        reason=f"Return refund DEBIT for return {return_req.return_number}",
                        user=user
                    )
                    return_req.settlement_adjustment_applied = True
                    return_req.save(update_fields=["settlement_adjustment_applied", "updated_at"])
                else:
                    from apps.settlements.models import UnappliedRefundAdjustment
                    UnappliedRefundAdjustment.objects.create(
                        vendor=return_req.vendor,
                        return_request=return_req,
                        vendor_order=return_req.vendor_order,
                        amount=refund_amount,
                        adjustment_type="DEBIT",
                        reason=f"Return refund DEBIT for return {return_req.return_number} (Deferred)",
                        is_applied=False
                    )
                    return_req.settlement_adjustment_applied = True
                    return_req.save(update_fields=["settlement_adjustment_applied", "updated_at"])
        else:
            return_req.status = ReturnRequest.REFUND_FAILED
            return_req.save(update_fields=["status", "updated_at"])

        return refund

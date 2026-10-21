from decimal import Decimal, ROUND_DOWN
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import Order, OrderItem, VendorOrder
from apps.vendors.models import VendorProduct, VendorProductVariant
from apps.vendors.services import record_inventory_movement


def create_split_vendor_orders(master_order, cart_items, user):
    """
    Groups cart items by vendor, allocates prices/discounts, creates VendorOrder sub-orders,
    populates snapshotted details, and creates OrderItems with stock reservations.
    """
    items_by_vendor = {}
    for item in cart_items:
        # Determine vendor from offer relation
        vendor = item.vendor_product.vendor
        if vendor not in items_by_vendor:
            items_by_vendor[vendor] = []
        items_by_vendor[vendor].append(item)

    vendor_list = list(items_by_vendor.keys())
    master_discount = master_order.discount
    master_subtotal = master_order.subtotal

    coupon = master_order.coupon
    funding_type = coupon.funding_type if (coupon and master_discount > Decimal("0.00")) else "NONE"
    coupon_vendor = coupon.vendor if coupon else None

    # Populate master order snapshot fields
    master_order.coupon_owner_vendor = coupon_vendor
    master_order.discount_funding_type = funding_type
    if funding_type == "VENDOR_FUNDED":
        master_order.vendor_discount_amount = master_discount
        master_order.platform_discount_amount = Decimal("0.00")
    elif funding_type == "PLATFORM_FUNDED":
        master_order.platform_discount_amount = master_discount
        master_order.vendor_discount_amount = Decimal("0.00")
    else:
        master_order.platform_discount_amount = Decimal("0.00")
        master_order.vendor_discount_amount = Decimal("0.00")
    
    master_order.save(update_fields=[
        "coupon_owner_vendor", "discount_funding_type",
        "platform_discount_amount", "vendor_discount_amount", "updated_at"
    ])

    allocated_discounts = {}
    running_discount_sum = Decimal("0.00")

    # Allocate discounts based on funding type
    for i, vendor in enumerate(vendor_list):
        items = items_by_vendor[vendor]
        vendor_subtotal = sum(
            (item.vendor_product_variant.price if item.vendor_product_variant_id else item.vendor_product.price) * item.quantity
            for item in items
        )
        
        if funding_type == "VENDOR_FUNDED":
            if coupon_vendor and vendor == coupon_vendor:
                vendor_discount = master_discount
                v_plat_discount = Decimal("0.00")
                v_vend_discount = master_discount
                v_fund_type = "VENDOR_FUNDED"
            else:
                vendor_discount = Decimal("0.00")
                v_plat_discount = Decimal("0.00")
                v_vend_discount = Decimal("0.00")
                v_fund_type = "NONE"
        elif funding_type == "PLATFORM_FUNDED":
            if i == len(vendor_list) - 1:
                vendor_discount = master_discount - running_discount_sum
            else:
                if master_subtotal > 0:
                    vendor_discount = (vendor_subtotal / master_subtotal * master_discount).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                else:
                    vendor_discount = Decimal("0.00")
                running_discount_sum += vendor_discount
            v_plat_discount = vendor_discount
            v_vend_discount = Decimal("0.00")
            v_fund_type = "PLATFORM_FUNDED"
        else:
            vendor_discount = Decimal("0.00")
            v_plat_discount = Decimal("0.00")
            v_vend_discount = Decimal("0.00")
            v_fund_type = "NONE"

        allocated_discounts[vendor] = {
            "subtotal": vendor_subtotal,
            "discount": vendor_discount,
            "platform_discount_amount": v_plat_discount,
            "vendor_discount_amount": v_vend_discount,
            "discount_funding_type": v_fund_type
        }

    # Create sub-orders & snapshot details
    for vendor, finances in allocated_discounts.items():
        sub_order = VendorOrder.objects.create(
            master_order=master_order,
            vendor=vendor,
            status=VendorOrder.PENDING,
            subtotal=finances["subtotal"],
            shipping_charge=Decimal("0.00"),  # shipping stays at master level
            discount=finances["discount"],
            platform_discount_amount=finances["platform_discount_amount"],
            vendor_discount_amount=finances["vendor_discount_amount"],
            discount_funding_type=finances["discount_funding_type"],
            tax=(finances["subtotal"] * Decimal("0.00")).quantize(Decimal("0.01")),  # tax calculation
            vendor_store_name_snapshot=vendor.store_name,
            vendor_business_name_snapshot=vendor.business_name,
        )

        order_items_to_create = []
        for item in items_by_vendor[vendor]:
            # Fetch price snapshot
            price = item.vendor_product_variant.price if item.vendor_product_variant_id else item.vendor_product.price
            if item.vendor_product_variant_id and item.vendor_product_variant:
                sku = item.vendor_product_variant.vendor_sku or (item.vendor_product_variant.product_variant.sku if item.vendor_product_variant.product_variant else "")
            elif item.vendor_product and item.vendor_product.product:
                v_first = item.vendor_product.product.variants.first()
                sku = v_first.sku if (v_first and v_first.sku) else (item.vendor_product.product.slug or "")
            else:
                sku = ""
            
            # Resolve image snapshot
            image_url = ""
            first_image = None
            if item.vendor_product_variant_id:
                first_image = item.vendor_product_variant.product_variant.images.first()
            if not first_image:
                v_first = item.vendor_product.product.variants.first()
                if v_first:
                    first_image = v_first.images.first()
            if first_image and first_image.image:
                try:
                    image_url = first_image.image.url
                except Exception:
                    image_url = ""

            product_name = item.vendor_product.product.name
            variant_name = item.vendor_product_variant.product_variant.name if item.vendor_product_variant_id else ""

            order_item = OrderItem(
                order=master_order,
                vendor_order=sub_order,
                product=item.vendor_product.product,
                variant=item.vendor_product_variant.product_variant if item.vendor_product_variant_id else None,
                product_name=product_name,
                variant_name=variant_name,
                sku=sku,
                product_image=image_url,
                price=price,
                quantity=item.quantity,
                total=price * item.quantity,
            )
            order_items_to_create.append(order_item)

            # Reservation: record inventory movement OUT
            record_inventory_movement(
                user=item.vendor_product.vendor.user,
                vendor_product_id=item.vendor_product.id,
                vendor_product_variant_id=item.vendor_product_variant.id if item.vendor_product_variant_id else None,
                movement_type="OUT",
                quantity=item.quantity,
                reason="OTHER",
                note=f"Vendor order {sub_order.vendor_order_number} created from master order {master_order.order_number}",
                reference_type="ORDER",
                reference_id=str(master_order.order_number)
            )

        OrderItem.objects.bulk_create(order_items_to_create)


def cancel_vendor_order_by_vendor(vendor_order, user):
    """
    Cancels a specific sub-order, releases/restores stock, and updates Master Order state.
    """
    if vendor_order.status in [VendorOrder.SHIPPED, VendorOrder.DELIVERED, VendorOrder.CANCELLED]:
        raise ValidationError(f"Sub-order in status '{vendor_order.get_status_display()}' cannot be cancelled.")

    if vendor_order.status == VendorOrder.CANCELLED:
        return

    with transaction.atomic():
        # lock vendor_order
        vendor_order = VendorOrder.objects.select_for_update().get(pk=vendor_order.pk)
        if vendor_order.status in [VendorOrder.SHIPPED, VendorOrder.DELIVERED, VendorOrder.CANCELLED]:
            raise ValidationError(f"Sub-order is already cancelled or shipped.")

        vendor_order.status = VendorOrder.CANCELLED
        vendor_order.save(update_fields=["status", "updated_at"])

        # Release stock
        for item in vendor_order.items.all():
            # Resolve vendor listing IDs
            if item.variant_id:
                vpv = VendorProductVariant.objects.filter(
                    product_variant_id=item.variant_id,
                    vendor_product__vendor=vendor_order.vendor
                ).first()
                vp_id = vpv.vendor_product_id if vpv else None
                vpv_id = vpv.id if vpv else None
            else:
                vp = VendorProduct.objects.filter(
                    product_id=item.product_id,
                    vendor=vendor_order.vendor
                ).first()
                vp_id = vp.id if vp else None
                vpv_id = None

            if vp_id:
                record_inventory_movement(
                    user=vendor_order.vendor.user,
                    vendor_product_id=vp_id,
                    vendor_product_variant_id=vpv_id,
                    movement_type="IN",
                    quantity=item.quantity,
                    reason="OTHER",
                    note=f"Cancellation of vendor order {vendor_order.vendor_order_number} from master order {vendor_order.master_order.order_number}",
                    reference_type="ORDER_CANCELLATION",
                    reference_id=str(vendor_order.master_order.order_number)
                )

        reconcile_master_order_status(vendor_order.master_order)

        from apps.notifications.services import NotificationService
        event_key = f"VENDOR_ORDER_CANCELLED:{vendor_order.vendor_order_number}"
        NotificationService.create_notification_on_commit(
            user=vendor_order.master_order.user,
            notification_type="ORDER",
            title="Sub-Order Cancelled",
            message=f"Sub-order {vendor_order.vendor_order_number} has been cancelled.",
            event_key=event_key,
            reference_type="VENDOR_ORDER",
            reference_id=vendor_order.vendor_order_number,
        )


def reconcile_master_order_status(master_order):
    """
    Dynamically recalculates Master Order status based on all constituent VendorOrder statuses.
    """
    sub_orders = list(master_order.vendor_orders.all())
    if not sub_orders:
        return

    statuses = [so.status for so in sub_orders]

    if all(s == VendorOrder.PENDING for s in statuses):
        master_order.status = Order.PENDING
    elif all(s == VendorOrder.CANCELLED for s in statuses):
        master_order.status = Order.CANCELLED
    elif all(s in [VendorOrder.CANCELLED, VendorOrder.DELIVERED] for s in statuses) and any(s == VendorOrder.DELIVERED for s in statuses):
        master_order.status = Order.DELIVERED
    elif all(s in [VendorOrder.SHIPPED, VendorOrder.DELIVERED, VendorOrder.CANCELLED] for s in statuses):
        master_order.status = Order.SHIPPED
    elif any(s in [VendorOrder.PROCESSING, VendorOrder.PACKED, VendorOrder.SHIPPED] for s in statuses):
        # We map mixed active processing states to PACKED as overall picking/packing state
        # or SHIPPED if shipped items exist.
        if any(s == VendorOrder.SHIPPED for s in statuses):
            master_order.status = Order.SHIPPED
        else:
            master_order.status = Order.PACKED
    elif all(s == VendorOrder.CONFIRMED for s in statuses):
        master_order.status = Order.CONFIRMED
    else:
        # Default fallback
        master_order.status = Order.CONFIRMED

    if master_order.status == Order.DELIVERED and master_order.payment_method == Order.COD and master_order.payment_status == Order.PAYMENT_PENDING:
        master_order.payment_status = Order.PAYMENT_PAID
        from apps.payments.models import Transaction
        Transaction.objects.filter(order=master_order, payment_method=Transaction.COD).update(status=Transaction.SUCCESS)
        master_order.save(update_fields=["status", "payment_status", "updated_at"])
    else:
        master_order.save(update_fields=["status", "updated_at"])


from datetime import timedelta

def expire_unpaid_orders(expiration_minutes=30):
    """
    Finds unpaid online orders older than the expiration threshold, locks them,
    cancels constituent sub-orders, releases reserved stock via record_inventory_movement(movement_type="IN"),
    and sets payment_status to PAYMENT_FAILED and order status to CANCELLED.
    """
    threshold = timezone.now() - timedelta(minutes=expiration_minutes)
    
    # Query candidate online orders
    expired_orders = Order.objects.filter(
        payment_status=Order.PAYMENT_PENDING,
        status=Order.PENDING,
        created_at__lte=threshold
    ).exclude(payment_method=Order.COD)

    expired_count = 0
    for order_obj in expired_orders:
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(
                    pk=order_obj.pk,
                    payment_status=Order.PAYMENT_PENDING,
                    status=Order.PENDING
                )
            except Order.DoesNotExist:
                continue

            for sub_order in order.vendor_orders.exclude(status=VendorOrder.CANCELLED):
                try:
                    cancel_vendor_order_by_vendor(sub_order, order.user)
                except Exception:
                    pass

            order.status = Order.CANCELLED
            order.payment_status = Order.PAYMENT_FAILED
            order.save(update_fields=["status", "payment_status", "updated_at"])
            expired_count += 1

    return expired_count


from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.core.signals import got_request_exception
from django.utils.timezone import now

# Import models
from apps.orders.models import Order
from apps.products.models import Product, ProductVariant
from apps.accounts.models import User
from apps.coupons.models import Coupon
from apps.payments.models import Transaction

# Import service
from .services import NotificationService
from .models import Notification

# ───────────────────────────────────────────────────────────────────────
# PRE-SAVE HANDLERS FOR CHANGE DETECTION
# ───────────────────────────────────────────────────────────────────────

@receiver(pre_save, sender=Order)
def cache_old_order_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = Order.objects.get(pk=instance.pk)
            instance._old_status = original.status
        except Order.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

@receiver(pre_save, sender=ProductVariant)
def cache_old_variant_stock(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = ProductVariant.objects.get(pk=instance.pk)
            instance._old_stock = original.stock
        except ProductVariant.DoesNotExist:
            instance._old_stock = None
    else:
        instance._old_stock = None

@receiver(pre_save, sender=Coupon)
def cache_old_coupon_usage_and_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = Coupon.objects.get(pk=instance.pk)
            instance._old_usage_count = original.usage_count
            instance._old_is_active = original.is_active
        except Coupon.DoesNotExist:
            instance._old_usage_count = None
            instance._old_is_active = None
    else:
        instance._old_usage_count = None
        instance._old_is_active = None

@receiver(pre_save, sender=Product)
def cache_old_product_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = Product.objects.get(pk=instance.pk)
            instance._old_is_active = original.is_active
        except Product.DoesNotExist:
            instance._old_is_active = None
    else:
        instance._old_is_active = None

@receiver(pre_save, sender=Transaction)
def cache_old_transaction_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            original = Transaction.objects.get(pk=instance.pk)
            instance._old_status = original.status
        except Transaction.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

# ───────────────────────────────────────────────────────────────────────
# POST-SAVE HANDLERS FOR NOTIFICATION CREATION
# ───────────────────────────────────────────────────────────────────────

@receiver(post_save, sender=Order)
def order_notifications(sender, instance, created, **kwargs):
    if created:
        NotificationService.create(
            title="New Order Placed",
            message=f"Order {instance.order_number} has been placed by {instance.user.email}.",
            notification_type=Notification.ORDER,
            priority=Notification.MEDIUM,
            reference_type="order",
            reference_id=str(instance.id),
            action_url=f"/admin/orders/{instance.id}",
            icon="shopping_cart"
        )
    else:
        old_status = getattr(instance, "_old_status", None)
        if old_status != instance.status:
            if instance.status == Order.CANCELLED:
                NotificationService.create(
                    title="Order Cancelled",
                    message=f"Order {instance.order_number} has been cancelled.",
                    notification_type=Notification.ORDER,
                    priority=Notification.HIGH,
                    reference_type="order",
                    reference_id=str(instance.id),
                    action_url=f"/admin/orders/{instance.id}",
                    icon="cancel"
                )
            elif instance.status == Order.DELIVERED:
                NotificationService.create(
                    title="Order Delivered",
                    message=f"Order {instance.order_number} has been successfully delivered.",
                    notification_type=Notification.ORDER,
                    priority=Notification.LOW,
                    reference_type="order",
                    reference_id=str(instance.id),
                    action_url=f"/admin/orders/{instance.id}",
                    icon="local_shipping"
                )
            elif instance.status == Order.REFUNDED:
                NotificationService.create(
                    title="Refund Processed",
                    message=f"Refund has been processed for Order {instance.order_number}.",
                    notification_type=Notification.ORDER,
                    priority=Notification.MEDIUM,
                    reference_type="order",
                    reference_id=str(instance.id),
                    action_url=f"/admin/orders/{instance.id}",
                    icon="settings_backup_restore"
                )

@receiver(post_save, sender=ProductVariant)
def inventory_notifications(sender, instance, created, **kwargs):
    old_stock = getattr(instance, "_old_stock", None)
    if created or old_stock != instance.stock:
        # Stock Updated Notification
        if not created:
            NotificationService.create(
                title="Stock Level Updated",
                message=f"Stock of variant {instance.product.name} ({instance.name}) changed from {old_stock} to {instance.stock}.",
                notification_type=Notification.INVENTORY,
                priority=Notification.LOW,
                reference_type="product_variant",
                reference_id=str(instance.id),
                action_url=f"/admin/products/{instance.product.id}",
                icon="inventory"
            )
        
        # Out of Stock Notification
        if instance.stock == 0:
            NotificationService.create(
                title="Variant Out Of Stock",
                message=f"Variant {instance.product.name} ({instance.name}) is now out of stock!",
                notification_type=Notification.INVENTORY,
                priority=Notification.HIGH,
                reference_type="product_variant",
                reference_id=str(instance.id),
                action_url=f"/admin/products/{instance.product.id}",
                icon="error_outline"
            )
        # Low Stock Notification
        elif 0 < instance.stock <= 10:
            NotificationService.create(
                title="Low Stock Warning",
                message=f"Variant {instance.product.name} ({instance.name}) is running low on stock ({instance.stock} left).",
                notification_type=Notification.INVENTORY,
                priority=Notification.MEDIUM,
                reference_type="product_variant",
                reference_id=str(instance.id),
                action_url=f"/admin/products/{instance.product.id}",
                icon="warning"
            )

@receiver(post_save, sender=User)
def customer_notifications(sender, instance, created, **kwargs):
    if created and instance.role == "customer":
        NotificationService.create(
            title="New Customer Registered",
            message=f"A new customer account has been registered: {instance.email}.",
            notification_type=Notification.CUSTOMER,
            priority=Notification.LOW,
            reference_type="user",
            reference_id=str(instance.id),
            action_url=f"/admin/customers/{instance.id}",
            icon="person_add"
        )

@receiver(post_save, sender=Coupon)
def coupon_notifications(sender, instance, created, **kwargs):
    # 1. Coupon Usage Limit Reached
    if instance.usage_limit is not None and instance.usage_count >= instance.usage_limit:
        old_usage_count = getattr(instance, "_old_usage_count", 0) or 0
        if old_usage_count < instance.usage_limit:
            NotificationService.create(
                title="Coupon Limit Reached",
                message=f"Coupon code {instance.code} has reached its usage limit of {instance.usage_limit}.",
                notification_type=Notification.COUPON,
                priority=Notification.MEDIUM,
                reference_type="coupon",
                reference_id=str(instance.id),
                action_url="/admin/coupons",
                icon="star"
            )

    # 2. Coupon Expired / Disabled
    old_is_active = getattr(instance, "_old_is_active", None)
    if old_is_active is True and instance.is_active is False:
        NotificationService.create(
            title="Coupon Deactivated",
            message=f"Coupon code {instance.code} has been deactivated or disabled.",
            notification_type=Notification.COUPON,
            priority=Notification.LOW,
            reference_type="coupon",
            reference_id=str(instance.id),
            action_url="/admin/coupons",
            icon="label_off"
        )

@receiver(post_save, sender=Product)
def product_notifications(sender, instance, created, **kwargs):
    old_is_active = getattr(instance, "_old_is_active", None)
    if not created and old_is_active != instance.is_active:
        if instance.is_active:
            NotificationService.create(
                title="Product Published",
                message=f"Product {instance.name} is now published and visible on the storefront.",
                notification_type=Notification.PRODUCT,
                priority=Notification.LOW,
                reference_type="product",
                reference_id=str(instance.id),
                action_url=f"/admin/products/{instance.id}",
                icon="publish"
            )
        else:
            NotificationService.create(
                title="Product Disabled",
                message=f"Product {instance.name} has been disabled and hidden from the storefront.",
                notification_type=Notification.PRODUCT,
                priority=Notification.LOW,
                reference_type="product",
                reference_id=str(instance.id),
                action_url=f"/admin/products/{instance.id}",
                icon="visibility_off"
            )

@receiver(post_save, sender=Transaction)
def payment_notifications(sender, instance, created, **kwargs):
    old_status = getattr(instance, "_old_status", None)
    if created or old_status != instance.status:
        if instance.status == Transaction.SUCCESS:
            NotificationService.create(
                title="Payment Success",
                message=f"Payment of {instance.amount} for Order {instance.order.order_number} succeeded (Txn: {instance.transaction_id}).",
                notification_type=Notification.PAYMENT,
                priority=Notification.LOW,
                reference_type="transaction",
                reference_id=str(instance.id),
                action_url=f"/admin/orders/{instance.order.id}",
                icon="check_circle"
            )
        elif instance.status == Transaction.FAILED:
            NotificationService.create(
                title="Payment Failed",
                message=f"Payment attempt of {instance.amount} for Order {instance.order.order_number} failed: {instance.error_message}.",
                notification_type=Notification.PAYMENT,
                priority=Notification.HIGH,
                reference_type="transaction",
                reference_id=str(instance.id),
                action_url=f"/admin/orders/{instance.order.id}",
                icon="error"
            )

# ───────────────────────────────────────────────────────────────────────
# SYSTEM ERROR EXCEPTION HANDLER
# ───────────────────────────────────────────────────────────────────────

@receiver(got_request_exception)
def server_error_notification(sender, request, **kwargs):
    # Retrieve details from exception if available
    import traceback
    tb = traceback.format_exc()
    path = request.path if request else "Unknown Path"
    
    NotificationService.create(
        title="Internal Server Error",
        message=f"An unhandled exception occurred during request to {path}. Stacktrace snippet: {tb[:200]}...",
        notification_type=Notification.SYSTEM,
        priority=Notification.HIGH,
        reference_type="system",
        reference_id="server_error",
        action_url="/admin/reports",
        icon="bug_report"
    )

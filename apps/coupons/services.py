from decimal import Decimal
from django.utils import timezone
from django.core.exceptions import ValidationError
from apps.orders.models import Order
from .models import Coupon, CouponUsage, CouponProduct, CouponCategory

class CouponValidationService:
    def _validate_dates(self, coupon):
        if not coupon.is_active:
            raise ValidationError("This coupon is currently inactive.")
        now = timezone.now()
        if coupon.start_date > now:
            raise ValidationError("This coupon is not yet valid.")
        if coupon.end_date < now:
            raise ValidationError("This coupon has expired.")

    def _validate_usage_limit(self, coupon):
        if coupon.has_reached_limit():
            raise ValidationError("This coupon has reached its maximum usage limit.")

    def _validate_per_user(self, coupon, user):
        if user and user.is_authenticated:
            user_usage_count = CouponUsage.objects.filter(coupon=coupon, user=user).count()
            if user_usage_count >= coupon.per_user_limit:
                raise ValidationError("You have already reached the usage limit for this coupon.")

    def _validate_first_order(self, coupon, user):
        if coupon.first_order_only:
            if not user or not user.is_authenticated:
                raise ValidationError("Authentication required for first order coupon.")
            # Check if user has any completed orders
            has_previous_orders = Order.objects.filter(user=user).exclude(status=Order.CANCELLED).exists()
            if has_previous_orders:
                raise ValidationError("This coupon is only valid for your first order.")

    def _validate_category(self, coupon, cart_items):
        if coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_CATEGORIES:
            allowed_category_ids = set(coupon.categories.values_list('id', flat=True))
            cart_category_ids = {item.variant.product.category_id for item in cart_items if item.variant.product and item.variant.product.category_id}
            if not allowed_category_ids.intersection(cart_category_ids):
                raise ValidationError("This coupon is not valid for any categories in your cart.")

    def _validate_products(self, coupon, cart_items):
        if coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_PRODUCTS:
            allowed_product_ids = set(coupon.products.values_list('id', flat=True))
            cart_product_ids = {item.variant.product_id for item in cart_items if item.variant.product_id}
            if not allowed_product_ids.intersection(cart_product_ids):
                raise ValidationError("This coupon is not valid for any products in your cart.")

    def _validate_product_combination(self, coupon, cart_items):
        if coupon.coupon_scope == Coupon.SCOPE_PRODUCT_COMBINATION:
            coupon_products = CouponProduct.objects.filter(coupon=coupon)
            cart_product_ids = {item.variant.product_id for item in cart_items if item.variant.product_id}
            for cp in coupon_products:
                if cp.product_id not in cart_product_ids:
                    raise ValidationError(f"This coupon requires the combination of specific products: {', '.join([p.name for p in coupon.products.all()])}.")

    def _validate_quantity(self, coupon, cart_items):
        # Validate minimum cart quantity
        if coupon.min_cart_quantity is not None:
            total_qty = sum(item.quantity for item in cart_items)
            if total_qty < coupon.min_cart_quantity:
                raise ValidationError(f"Minimum cart quantity of {coupon.min_cart_quantity} products required.")

        # Validate minimum product quantity
        if coupon.min_product_quantity is not None:
            if coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_PRODUCTS:
                target_product_ids = set(coupon.products.values_list('id', flat=True))
                target_qty = sum(item.quantity for item in cart_items if item.variant.product_id in target_product_ids)
                if target_qty < coupon.min_product_quantity:
                    raise ValidationError(f"Minimum quantity of {coupon.min_product_quantity} target products required.")
            elif coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_CATEGORIES:
                target_category_ids = set(coupon.categories.values_list('id', flat=True))
                target_qty = sum(item.quantity for item in cart_items if item.variant.product.category_id in target_category_ids)
                if target_qty < coupon.min_product_quantity:
                    raise ValidationError(f"Minimum quantity of {coupon.min_product_quantity} target category products required.")

        # Validate combination quantities
        if coupon.coupon_scope == Coupon.SCOPE_PRODUCT_COMBINATION:
            coupon_products = CouponProduct.objects.filter(coupon=coupon)
            for cp in coupon_products:
                cart_item = next((item for item in cart_items if item.variant.product_id == cp.product_id), None)
                if not cart_item or cart_item.quantity < cp.min_quantity:
                    raise ValidationError(f"Minimum quantity of {cp.min_quantity} required for {cp.product.name}.")

    def _validate_cart_amount(self, coupon, subtotal):
        if subtotal < coupon.min_purchase_amount:
            raise ValidationError(f"Minimum purchase amount of ₹{coupon.min_purchase_amount} required to use this coupon.")
        if coupon.max_purchase_amount is not None and subtotal > coupon.max_purchase_amount:
            raise ValidationError(f"Subtotal exceeds the maximum purchase amount of ₹{coupon.max_purchase_amount} allowed for this coupon.")

    def _calculate_discount(self, coupon, cart_items, subtotal):
        if coupon.discount_type == Coupon.DISCOUNT_FREE_SHIPPING:
            # Free Shipping saves ₹99 if subtotal is below the free shipping threshold (₹999)
            if subtotal >= Decimal("999.00"):
                return Decimal("0.00")
            return Decimal("99.00")

        # Determine qualifying subtotal
        if coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_CATEGORIES:
            allowed_category_ids = set(coupon.categories.values_list('id', flat=True))
            qualifying_subtotal = sum(
                item.variant.price * item.quantity 
                for item in cart_items 
                if item.variant.product.category_id in allowed_category_ids
            )
        elif coupon.coupon_scope == Coupon.SCOPE_SPECIFIC_PRODUCTS:
            allowed_product_ids = set(coupon.products.values_list('id', flat=True))
            qualifying_subtotal = sum(
                item.variant.price * item.quantity 
                for item in cart_items 
                if item.variant.product_id in allowed_product_ids
            )
        elif coupon.coupon_scope == Coupon.SCOPE_PRODUCT_COMBINATION:
            # Combination coupon discount applies to the combination products subtotal
            allowed_product_ids = set(coupon.products.values_list('id', flat=True))
            qualifying_subtotal = sum(
                item.variant.price * item.quantity 
                for item in cart_items 
                if item.variant.product_id in allowed_product_ids
            )
        else:
            qualifying_subtotal = subtotal

        if qualifying_subtotal <= 0:
            return Decimal("0.00")

        if coupon.discount_type == Coupon.DISCOUNT_PERCENTAGE:
            discount = (qualifying_subtotal * (coupon.discount_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            if coupon.max_discount_amount is not None:
                discount = min(discount, coupon.max_discount_amount)
            return discount
        elif coupon.discount_type == Coupon.DISCOUNT_FIXED:
            return min(coupon.discount_value, qualifying_subtotal)
            
        return Decimal("0.00")

    def validate(self, coupon, user, cart_items):
        if not cart_items:
            raise ValidationError("Your cart is empty.")

        subtotal = sum(item.variant.price * item.quantity for item in cart_items)

        # Run independent validations
        self._validate_dates(coupon)
        self._validate_usage_limit(coupon)
        self._validate_per_user(coupon, user)
        self._validate_first_order(coupon, user)
        self._validate_category(coupon, cart_items)
        self._validate_products(coupon, cart_items)
        self._validate_product_combination(coupon, cart_items)
        self._validate_quantity(coupon, cart_items)
        self._validate_cart_amount(coupon, subtotal)

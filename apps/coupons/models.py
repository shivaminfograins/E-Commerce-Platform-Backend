from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

class Coupon(models.Model):
    DISCOUNT_PERCENTAGE = 'percentage'
    DISCOUNT_FIXED = 'fixed'
    
    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_PERCENTAGE, 'Percentage'),
        (DISCOUNT_FIXED, 'Fixed Amount'),
    ]

    code = models.CharField(
        max_length=50, 
        unique=True, 
        db_index=True,
        help_text="Unique, case-insensitive coupon code."
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the coupon."
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DISCOUNT_TYPE_CHOICES,
        default=DISCOUNT_PERCENTAGE,
        help_text="Type of discount (percentage or fixed amount)."
    )
    discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="The value of the discount (e.g. 10.00 for 10% or $10.00)."
    )
    min_purchase_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Minimum order subtotal required to apply this coupon."
    )
    max_discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Maximum discount cap (applicable for percentage discount)."
    )
    start_date = models.DateTimeField(
        help_text="DateTime when the coupon becomes valid."
    )
    end_date = models.DateTimeField(
        help_text="DateTime when the coupon expires."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Toggle coupon status."
    )
    usage_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total usage limit across all customers. None means unlimited."
    )
    per_user_limit = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times a single customer can use this coupon."
    )
    usage_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this coupon has been used."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Coupon"
        verbose_name_plural = "Coupons"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"start_date": "Start date must be before end date."})
        if self.discount_type == self.DISCOUNT_PERCENTAGE and self.discount_value > Decimal("100.00"):
            raise ValidationError({"discount_value": "Percentage discount cannot exceed 100%."})
        if self.discount_value <= Decimal("0.00"):
            raise ValidationError({"discount_value": "Discount value must be greater than zero."})

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def is_valid_now(self):
        now = timezone.now()
        return self.is_active and self.start_date <= now <= self.end_date

    def has_reached_limit(self):
        if self.usage_limit is not None:
            return self.usage_count >= self.usage_limit
        return False

    def calculate_discount(self, subtotal):
        if subtotal < self.min_purchase_amount:
            return Decimal("0.00")

        if self.discount_type == self.DISCOUNT_PERCENTAGE:
            discount = (subtotal * (self.discount_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            if self.max_discount_amount is not None:
                discount = min(discount, self.max_discount_amount)
            return discount
        elif self.discount_type == self.DISCOUNT_FIXED:
            return min(self.discount_value, subtotal)
        return Decimal("0.00")

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()}: {self.discount_value})"


class CouponUsage(models.Model):
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name="usages"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coupon_usages"
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="coupon_usages"
    )
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Coupon Usage"
        verbose_name_plural = "Coupon Usages"

    def __str__(self):
        return f"{self.user} used {self.coupon.code} on Order #{self.order.order_number}"

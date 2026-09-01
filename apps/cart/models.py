from django.db import models
from django.conf import settings
from apps.products.models import ProductVariant


# ----------------------------------------------------------
# Guest Model
# Represents an anonymous (unauthenticated) shopper.
# A unique token (e.g. UUID) is generated on the frontend
# and sent with every request so we can group their cart items.
class Guest(models.Model):
    guest_token = models.CharField(max_length=255, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Guest"
        verbose_name_plural = "Guests"

    def __str__(self):
        return f"Guest {self.guest_token[:8]}"


# ----------------------------------------------------------
# CartItem Model
# One row per (owner, variant) pair.
# Owner is EITHER a logged-in user OR a guest — never both,
# and never neither (enforced by the UniqueConstraints below).
class CartItem(models.Model):
    # ── Owner fields ─────────────────────────────────────────
    # Exactly one of `user` or `guest` must be set.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,   # deleting the user wipes their cart
        null=True,
        blank=True,
        related_name="cart_items",
    )
    guest = models.ForeignKey(
        Guest,
        on_delete=models.CASCADE,   # deleting the guest token wipes their cart
        null=True,
        blank=True,
        related_name="cart_items",
    )

    # ── Product reference ─────────────────────────────────────
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="cart_items",
        help_text="The specific product variant (size, colour, etc.) added to the cart.",
        null=True,
        blank=True
    )
    vendor_product = models.ForeignKey(
        "vendors.VendorProduct",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items"
    )
    vendor_product_variant = models.ForeignKey(
        "vendors.VendorProductVariant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items"
    )

    # ── Quantity ──────────────────────────────────────────────
    quantity = models.PositiveIntegerField(default=1)

    # ── Timestamps ────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Meta ─────────────────────────────────────────────────
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Cart Item"
        verbose_name_plural = "Cart Items"

        constraints = [
            models.UniqueConstraint(
                fields=["user", "vendor_product_variant"],
                condition=models.Q(user__isnull=False, vendor_product_variant__isnull=False),
                name="unique_user_vendor_variant",
            ),
            models.UniqueConstraint(
                fields=["user", "vendor_product"],
                condition=models.Q(user__isnull=False, vendor_product_variant__isnull=True, vendor_product__isnull=False),
                name="unique_user_vendor_product",
            ),
            models.UniqueConstraint(
                fields=["guest", "vendor_product_variant"],
                condition=models.Q(guest__isnull=False, vendor_product_variant__isnull=False),
                name="unique_guest_vendor_variant",
            ),
            models.UniqueConstraint(
                fields=["guest", "vendor_product"],
                condition=models.Q(guest__isnull=False, vendor_product_variant__isnull=True, vendor_product__isnull=False),
                name="unique_guest_vendor_product",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        
        if not self.vendor_product_variant_id and not self.vendor_product_id and self.variant_id:
            from apps.vendors.models import VendorProductVariant
            vpv = VendorProductVariant.objects.filter(
                product_variant_id=self.variant_id,
                vendor_product__vendor__status="APPROVED",
                vendor_product__status="ACTIVE",
                is_active=True
            ).first()
            if vpv:
                self.vendor_product_variant = vpv
                self.vendor_product = vpv.vendor_product

        if self.vendor_product_variant_id:
            # Variant product flow
            vpv = self.vendor_product_variant
            if self.vendor_product_id is None:
                self.vendor_product = vpv.vendor_product
            elif vpv.vendor_product_id != self.vendor_product_id:
                raise ValidationError("vendor_product_variant must belong to the selected vendor_product.")
                
            if vpv.product_variant.product_id != self.vendor_product.product_id:
                raise ValidationError("Variant and product mismatch.")
                
            if self.vendor_product.vendor.status != "APPROVED":
                raise ValidationError("Vendor is not approved.")
                
            if not vpv.is_active or self.vendor_product.status != "ACTIVE":
                raise ValidationError("Vendor offer is inactive.")
                
            # Populate legacy variant field for fallback queries
            self.variant = vpv.product_variant

        elif self.vendor_product_id:
            # Simple product flow
            vp = self.vendor_product
            if vp.product.variants.exists():
                raise ValidationError("Selected listing has variants; vendor_product_variant must be populated.")
                
            if vp.vendor.status != "APPROVED":
                raise ValidationError("Vendor is not approved.")
                
            if vp.status != "ACTIVE":
                raise ValidationError("Vendor offer is inactive.")
                
            self.variant = None
        else:
            raise ValidationError("A specific vendor offer must be selected.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    # ── String representation ─────────────────────────────────
    def __str__(self):
        if self.user_id:
            owner = self.user.username
        else:
            owner = f"Guest {self.guest.guest_token[:8]}"

        item_str = str(self.vendor_product_variant) if self.vendor_product_variant_id else str(self.vendor_product)
        return f"{owner} → {item_str} × {self.quantity}"


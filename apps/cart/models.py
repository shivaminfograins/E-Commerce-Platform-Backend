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
    # We now link to ProductVariant (not Product) because the
    # variant is the purchasable unit that carries price, SKU,
    # and stock level.  Deleting a variant removes it from
    # every cart that contained it (CASCADE).
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="cart_items",
        help_text="The specific product variant (size, colour, etc.) added to the cart.",
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
            # Prevent a logged-in user from adding the same variant twice.
            # Instead of two rows with quantity=2 and quantity=3, we want
            # one row with quantity=5 (handled in the view/serializer layer).
            models.UniqueConstraint(
                fields=["user", "variant"],
                condition=models.Q(user__isnull=False),
                name="unique_user_variant",
            ),
            # Same deduplication rule for guest carts.
            models.UniqueConstraint(
                fields=["guest", "variant"],
                condition=models.Q(guest__isnull=False),
                name="unique_guest_variant",
            ),
        ]

    # ── String representation ─────────────────────────────────
    def __str__(self):
        # Resolve owner label: username for authenticated users,
        # abbreviated token for guests.
        if self.user_id:
            owner = self.user.username
        else:
            owner = f"Guest {self.guest.guest_token[:8]}"

        # variant.__str__() returns "ProductName - VariantName"
        return f"{owner} → {self.variant} × {self.quantity}"

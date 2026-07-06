from django.db import models
from django.conf import settings
from apps.products.models import Product

class Guest(models.Model):
    guest_token = models.CharField(max_length=255, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guest {self.guest_token[:8]}"

class CartItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items"
    )
    guest = models.ForeignKey(
        Guest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="cart_items"
    )
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        owner = self.user.username if self.user else f"Guest {self.guest.guest_token[:8]}"
        return f"{owner} - {self.product.name} ({self.quantity})"

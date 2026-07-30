from django.db import models

class Notification(models.Model):
    # Notification Types
    ORDER = "ORDER"
    PAYMENT = "PAYMENT"
    CUSTOMER = "CUSTOMER"
    PRODUCT = "PRODUCT"
    INVENTORY = "INVENTORY"
    COUPON = "COUPON"
    REVIEW = "REVIEW"
    SYSTEM = "SYSTEM"

    NOTIFICATION_TYPE_CHOICES = [
        (ORDER, "Order"),
        (PAYMENT, "Payment"),
        (CUSTOMER, "Customer"),
        (PRODUCT, "Product"),
        (INVENTORY, "Inventory"),
        (COUPON, "Coupon"),
        (REVIEW, "Review"),
        (SYSTEM, "System"),
    ]

    # Priority Levels
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    PRIORITY_CHOICES = [
        (LOW, "Low"),
        (MEDIUM, "Medium"),
        (HIGH, "High"),
    ]

    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20,
        choices=NOTIFICATION_TYPE_CHOICES,
        default=SYSTEM,
        db_index=True
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default=MEDIUM
    )
    
    # Generic references to allow linking to any entity
    reference_type = models.CharField(max_length=50, null=True, blank=True)
    reference_id = models.CharField(max_length=100, null=True, blank=True)
    action_url = models.CharField(max_length=255, null=True, blank=True)
    icon = models.CharField(max_length=50, null=True, blank=True)
    
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self):
        return f"[{self.notification_type}] {self.title} - {'Read' if self.is_read else 'Unread'}"

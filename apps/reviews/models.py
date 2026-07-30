from django.db import models
from django.conf import settings
from apps.products.models import Product, ProductVariant
from apps.orders.models import Order
from django.db.models import Avg, Count
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

class ProductReview(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    HIDDEN = "hidden"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (HIDDEN, "Hidden"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews"
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews"
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        choices=[(i, str(i)) for i in range(1, 6)]
    )
    title = models.CharField(
        max_length=255
    )
    comment = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=APPROVED
    )
    is_verified_purchase = models.BooleanField(
        default=False
    )
    helpful_count = models.PositiveIntegerField(
        default=0
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("product", "user")

    def __str__(self):
        return f"{self.user.username} - {self.product.name} ({self.rating}★)"


class ReviewImage(models.Model):
    review = models.ForeignKey(
        ProductReview,
        on_delete=models.CASCADE,
        related_name="images"
    )
    image = models.ImageField(
        upload_to="reviews/"
    )

    def __str__(self):
        return f"Image for Review #{self.review.id}"


class ReviewHelpful(models.Model):
    review = models.ForeignKey(
        ProductReview,
        on_delete=models.CASCADE,
        related_name="helpful_votes"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="helpful_reviews"
    )

    class Meta:
        unique_together = ("review", "user")

    def __str__(self):
        return f"{self.user.username} helpful on Review #{self.review.id}"


class ReviewReport(models.Model):
    SPAM = "spam"
    FAKE = "fake"
    ABUSIVE = "abusive"
    OFF_TOPIC = "off_topic"
    OTHER = "other"

    REASON_CHOICES = [
        (SPAM, "Spam"),
        (FAKE, "Fake Review"),
        (ABUSIVE, "Abusive Content"),
        (OFF_TOPIC, "Off Topic"),
        (OTHER, "Other"),
    ]

    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RESOLVED, "Resolved"),
        (REJECTED, "Rejected"),
    ]

    review = models.ForeignKey(
        ProductReview,
        on_delete=models.CASCADE,
        related_name="reports"
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reported_reviews"
    )
    reason = models.CharField(
        max_length=50,
        choices=REASON_CHOICES
    )
    comment = models.TextField(
        blank=True,
        default=""
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"Report on Review #{self.review.id} by {self.reported_by.username}"


# ── Statistics Recalculation Helpers & Signals ──

def recalculate_product_ratings(product_id):
    # We only count APPROVED reviews!
    approved_reviews = ProductReview.objects.filter(product_id=product_id, status=ProductReview.APPROVED)
    
    counts = approved_reviews.aggregate(
        avg_rating=Avg('rating'),
        total=Count('id')
    )
    
    avg_rating = counts['avg_rating'] or 0.00
    total = counts['total'] or 0
    
    # Calculate counts for 1 to 5 stars
    stars = approved_reviews.values('rating').annotate(count=Count('id'))
    star_map = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for item in stars:
        star_map[item['rating']] = item['count']
        
    Product.objects.filter(id=product_id).update(
        average_rating=avg_rating,
        total_reviews=total,
        rating_1_count=star_map[1],
        rating_2_count=star_map[2],
        rating_3_count=star_map[3],
        rating_4_count=star_map[4],
        rating_5_count=star_map[5]
    )

@receiver(post_save, sender=ProductReview)
def review_saved(sender, instance, **kwargs):
    recalculate_product_ratings(instance.product_id)

@receiver(post_delete, sender=ProductReview)
def review_deleted(sender, instance, **kwargs):
    recalculate_product_ratings(instance.product_id)

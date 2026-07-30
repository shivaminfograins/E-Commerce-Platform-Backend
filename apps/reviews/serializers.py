from rest_framework import serializers
from .models import ProductReview, ReviewImage, ReviewReport, ReviewHelpful
from apps.orders.models import Order, OrderItem
from apps.products.models import ProductVariant

class ReviewImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewImage
        fields = ["id", "image"]


class ProductReviewSerializer(serializers.ModelSerializer):
    images = ReviewImageSerializer(many=True, read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    variant_name = serializers.CharField(source="variant.name", default="", read_only=True)
    has_voted_helpful = serializers.SerializerMethodField()

    class Meta:
        model = ProductReview
        fields = [
            "id",
            "product",
            "product_name",
            "variant",
            "variant_name",
            "order",
            "user",
            "username",
            "rating",
            "title",
            "comment",
            "status",
            "is_verified_purchase",
            "helpful_count",
            "images",
            "has_voted_helpful",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user", "status", "is_verified_purchase", "helpful_count", "order"]

    def get_has_voted_helpful(self, obj):
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            return ReviewHelpful.objects.filter(review=obj, user=request.user).exists()
        return False

    def validate(self, attrs):
        request = self.context.get("request")
        if not request or not request.user:
            raise serializers.ValidationError("Authentication credentials were not provided.")

        user = request.user
        product = attrs.get("product")
        variant = attrs.get("variant")

        # If it is an update, we skip the purchase validation since it was verified on creation
        if self.instance:
            return attrs

        # Verify purchase logic:
        delivered_orders = Order.objects.filter(user=user, status=Order.DELIVERED)
        if not delivered_orders.exists():
            raise serializers.ValidationError("You must have a delivered order for this product to write a review.")

        # Find matching order item
        matching_item = OrderItem.objects.filter(
            order__in=delivered_orders,
            product=product
        ).select_related("order").first()

        if not matching_item:
            raise serializers.ValidationError("You can only review products that you have purchased.")

        # Attach order and variant automatically
        attrs["order"] = matching_item.order
        if not variant and matching_item.variant:
            attrs["variant"] = matching_item.variant

        attrs["user"] = user
        attrs["is_verified_purchase"] = True
        attrs["status"] = ProductReview.APPROVED  # Auto-approve reviews

        # Check for duplicates (Rule 4: One review per user per product)
        if ProductReview.objects.filter(product=product, user=user).exists():
            raise serializers.ValidationError("You have already reviewed this product. Please edit your existing review.")

        return attrs


class ReviewReportSerializer(serializers.ModelSerializer):
    reported_by_username = serializers.CharField(source="reported_by.username", read_only=True)

    class Meta:
        model = ReviewReport
        fields = [
            "id",
            "review",
            "reported_by",
            "reported_by_username",
            "reason",
            "comment",
            "status",
            "created_at",
        ]
        read_only_fields = ["reported_by", "status"]

    def validate(self, attrs):
        request = self.context.get("request")
        if not request or not request.user:
            raise serializers.ValidationError("Authentication required.")
        attrs["reported_by"] = request.user
        return attrs

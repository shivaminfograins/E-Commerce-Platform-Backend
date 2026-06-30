from rest_framework import serializers
from .models import WishlistItem
from apps.products.serializers import ProductSerializer

class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ["id", "product", "created_at"]
        read_only_fields = ["id", "product", "created_at"]

class WishlistItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WishlistItem
        fields = ["product"]

    def validate(self, attrs):
        user = self.context["request"].user
        product = attrs["product"]
        if WishlistItem.objects.filter(user=user, product=product).exists():
            raise serializers.ValidationError("This product is already in your wishlist.")
        return attrs

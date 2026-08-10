from django.core.exceptions import ValidationError
from rest_framework import serializers
from django.utils import timezone
from .models import Coupon, CouponUsage, CouponProduct
from apps.products.models import Category, Product

class CouponProductSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')

    class Meta:
        model = CouponProduct
        fields = ['product', 'product_name', 'min_quantity']


class CouponSerializer(serializers.ModelSerializer):
    is_expired = serializers.SerializerMethodField()
    categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Category.objects.all(), required=False
    )
    product_combinations = CouponProductSerializer(
        source='couponproduct_set', many=True, required=False
    )

    class Meta:
        model = Coupon
        fields = [
            'id',
            'code',
            'description',
            'discount_type',
            'discount_value',
            'min_purchase_amount',
            'max_purchase_amount',
            'max_discount_amount',
            'start_date',
            'end_date',
            'is_active',
            'usage_limit',
            'per_user_limit',
            'usage_count',
            'is_expired',
            'coupon_scope',
            'categories',
            'product_combinations',
            'min_cart_quantity',
            'min_product_quantity',
            'first_order_only',
            'priority',
            'auto_apply',
            'is_stackable',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']

    def get_is_expired(self, obj):
        return obj.end_date < timezone.now()

    def validate(self, data):
        # We need a temp dict copy because we pop M2M fields out of standard model init
        temp_data = dict(data)
        temp_data.pop('categories', None)
        temp_data.pop('couponproduct_set', None)
        
        instance = Coupon(**temp_data)
        try:
            instance.clean()
        except ValidationError as e:
            raise serializers.ValidationError(str(e))
        return data

    def create(self, validated_data):
        categories_data = validated_data.pop('categories', [])
        product_combinations_data = validated_data.pop('couponproduct_set', [])
        
        coupon = Coupon.objects.create(**validated_data)
        
        # Set categories
        coupon.categories.set(categories_data)
        
        # Create CouponProduct links
        for pc_data in product_combinations_data:
            CouponProduct.objects.create(
                coupon=coupon,
                product=pc_data['product'],
                min_quantity=pc_data.get('min_quantity', 1)
            )
            
        return coupon

    def update(self, instance, validated_data):
        categories_data = validated_data.pop('categories', None)
        product_combinations_data = validated_data.pop('couponproduct_set', None)
        
        # Update standard fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update categories if provided
        if categories_data is not None:
            instance.categories.set(categories_data)
            
        # Update product combinations if provided
        if product_combinations_data is not None:
            CouponProduct.objects.filter(coupon=instance).delete()
            for pc_data in product_combinations_data:
                CouponProduct.objects.create(
                    coupon=instance,
                    product=pc_data['product'],
                    min_quantity=pc_data.get('min_quantity', 1)
                )
                
        return instance


class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, max_length=50)

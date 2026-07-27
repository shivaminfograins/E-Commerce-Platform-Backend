from rest_framework import serializers
from django.utils import timezone
from .models import Coupon, CouponUsage

class CouponSerializer(serializers.ModelSerializer):
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            'id',
            'code',
            'description',
            'discount_type',
            'discount_value',
            'min_purchase_amount',
            'max_discount_amount',
            'start_date',
            'end_date',
            'is_active',
            'usage_limit',
            'per_user_limit',
            'usage_count',
            'is_expired',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']

    def get_is_expired(self, obj):
        return obj.end_date < timezone.now()

    def validate(self, data):
        # Trigger model clean logic to run validations
        instance = Coupon(**data)
        try:
            instance.clean()
        except serializers.ValidationError as e:
            raise e
        except Exception as e:
            raise serializers.ValidationError(str(e))
        return data


class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, max_length=50)

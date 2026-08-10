from django.contrib import admin
from .models import Coupon, CouponUsage, CouponProduct, CouponCategory

class CouponProductInline(admin.TabularInline):
    model = CouponProduct
    extra = 1

class CouponCategoryInline(admin.TabularInline):
    model = CouponCategory
    extra = 1

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'discount_type',
        'discount_value',
        'coupon_scope',
        'is_active',
        'start_date',
        'end_date',
        'usage_count',
        'usage_limit',
        'priority'
    )
    list_filter = ('is_active', 'discount_type', 'coupon_scope', 'first_order_only')
    search_fields = ('code', 'description')
    inlines = [CouponProductInline, CouponCategoryInline]

@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ('coupon', 'user', 'order', 'used_at')
    list_filter = ('used_at',)
    search_fields = ('coupon__code', 'user__username', 'order__order_number')

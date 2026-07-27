from django.urls import path
from .views import CouponValidateView, CouponApplyView

urlpatterns = [
    path("validate/", CouponValidateView.as_view(), name="coupon-validate"),
    path("apply/", CouponApplyView.as_view(), name="coupon-apply"),
]

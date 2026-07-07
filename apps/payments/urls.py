"""
apps/payments/urls.py
=====================
"""

from django.urls import path
from .views import PaymentVerificationAPIView, RazorpayWebhookAPIView

app_name = "payments"

urlpatterns = [
    path("verify/", PaymentVerificationAPIView.as_view(), name="verify_payment"),
    path("webhook/", RazorpayWebhookAPIView.as_view(), name="razorpay_webhook"),
]

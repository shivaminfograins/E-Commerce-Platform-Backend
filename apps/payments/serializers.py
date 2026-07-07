"""
apps/payments/serializers.py
=============================
"""

from rest_framework import serializers


class PaymentVerificationSerializer(serializers.Serializer):
    """
    Validates Razorpay payment signature fields sent from the frontend checkout client.
    """
    razorpay_order_id  = serializers.CharField(required=True)
    razorpay_payment_id = serializers.CharField(required=True)
    razorpay_signature  = serializers.CharField(required=True)

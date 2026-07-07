"""
apps/payments/services.py
==========================
Production-ready Razorpay payment client integration.

Using python standard libraries for crypto signature verification. This eliminates
external package dependency version issues and keeps the execution fast.
"""

import hmac
import hashlib
import requests
from decimal import Decimal
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class RazorpayService:
    """
    Client wrapper for Razorpay REST APIs.
    """
    @staticmethod
    def _get_credentials():
        key_id = getattr(settings, "RAZORPAY_KEY_ID", "") or "rzp_test_mockkeyid123"
        key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "") or "mocksecret123456789"
        return key_id, key_secret

    @classmethod
    def create_razorpay_order(cls, order_number: str, amount: Decimal) -> dict:
        """
        Creates an order in Razorpay.
        Amount must be converted to paise (1 INR = 100 paise).
        """
        key_id, key_secret = cls._get_credentials()
        
        # Razorpay expects integer amount in paise
        amount_paise = int(amount * Decimal("100"))
        
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": order_number,
            "payment_capture": 1,  # Auto capture payment
        }

        try:
            # We use direct request call for robust control and logging
            response = requests.post(
                "https://api.razorpay.com/v1/orders",
                json=payload,
                auth=(key_id, key_secret),
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "id": data.get("id"),
                    "amount": amount,
                    "currency": "INR",
                    "raw_response": data,
                }
            else:
                logger.error(f"Razorpay order creation failed: {response.text}")
                return {
                    "success": False,
                    "error": response.text,
                    "raw_response": response.json() if response.headers.get("content-type") == "application/json" else {}
                }
        except Exception as e:
            logger.exception("Error connecting to Razorpay API")
            return {
                "success": False,
                "error": str(e),
                "raw_response": {}
            }

    @classmethod
    def verify_payment_signature(cls, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Verifies the authenticity of a Razorpay payment response signature.
        Formula: HMAC-SHA256(razorpay_order_id + "|" + razorpay_payment_id, key_secret)
        """
        _, key_secret = cls._get_credentials()
        
        msg = f"{razorpay_order_id}|{razorpay_payment_id}"
        
        try:
            generated_signature = hmac.new(
                key_secret.encode("utf-8"),
                msg.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            
            # Constant time comparison to prevent timing attacks
            return hmac.compare_digest(generated_signature, razorpay_signature)
        except Exception as e:
            logger.error(f"Signature verification failed with exception: {e}")
            return False

    @classmethod
    def verify_webhook_signature(cls, payload_body: bytes, signature_header: str) -> bool:
        """
        Verifies the authenticity of incoming Razorpay webhook events.
        """
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "") or "mockwebhooksecret123"
        
        try:
            generated_signature = hmac.new(
                webhook_secret.encode("utf-8"),
                payload_body,
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(generated_signature, signature_header)
        except Exception as e:
            logger.error(f"Webhook signature verification exception: {e}")
            return False

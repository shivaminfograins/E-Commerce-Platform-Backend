"""
apps/payments/views.py
======================
Production-ready views for payment verification and webhooks.
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.db import transaction
from django.shortcuts import get_object_or_404
import logging
import json

from .models import Transaction
from apps.orders.models import Order
from .serializers import PaymentVerificationSerializer
from .services import RazorpayService

logger = logging.getLogger(__name__)


class PaymentVerificationAPIView(APIView):
    """
    POST /api/payments/verify/
    Verifies payment signatures sent by frontend after completing checkout modal.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data["razorpay_order_id"]
        payment_id = serializer.validated_data["razorpay_payment_id"]
        signature = serializer.validated_data["razorpay_signature"]

        # Resolve transaction record
        db_transaction = get_object_or_404(
            Transaction,
            razorpay_order_id=order_id,
            order__user=request.user
        )

        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=signature
        )

        if not is_valid:
            with transaction.atomic():
                db_transaction.status = Transaction.FAILED
                db_transaction.error_message = "Signature verification failed."
                db_transaction.save(update_fields=["status", "error_message", "updated_at"])
                
                # Mark order payment as failed
                order = db_transaction.order
                order.payment_status = Order.PAYMENT_FAILED
                order.save(update_fields=["payment_status", "updated_at"])

            return Response(
                {"success": False, "message": "Signature verification failed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update records on successful payment signature
        with transaction.atomic():
            db_transaction.status = Transaction.SUCCESS
            db_transaction.transaction_id = payment_id
            db_transaction.save(update_fields=["status", "transaction_id", "updated_at"])

            order = db_transaction.order
            order.payment_status = Order.PAYMENT_PAID
            # Move from pending to confirmed upon payment clearance
            if order.status == Order.PENDING:
                order.status = Order.CONFIRMED
            order.save(update_fields=["payment_status", "status", "updated_at"])

        return Response({
            "success": True,
            "message": "Payment verified and recorded successfully.",
            "order_number": order.order_number
        }, status=status.HTTP_200_OK)


class RazorpayWebhookAPIView(APIView):
    """
    POST /api/payments/webhook/
    Public API webhook receiver endpoint.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature") or ""
        
        if not signature:
            logger.error("Missing X-Razorpay-Signature header in webhook.")
            return Response({"error": "Signature missing"}, status=status.HTTP_400_BAD_REQUEST)

        # Verify payload integrity
        is_valid = RazorpayService.verify_webhook_signature(
            payload_body=request.body,
            signature_header=signature
        )

        if not is_valid:
            logger.error("Invalid webhook signature.")
            return Response({"error": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            event_data = json.loads(request.body.decode("utf-8"))
            event_type = event_data.get("event")
            
            logger.info(f"Processing webhook event: {event_type}")

            payload = event_data.get("payload", {})
            payment_entity = payload.get("payment", {}).get("entity", {})
            order_entity = payload.get("order", {}).get("entity", {})

            # Extract identifier keys
            rzp_order_id = payment_entity.get("order_id") or order_entity.get("id")
            payment_id = payment_entity.get("id")

            if not rzp_order_id:
                logger.warning("No order reference found in webhook payload.")
                return Response({"status": "ignored"}, status=status.HTTP_200_OK)

            # Match pending transaction registry
            db_transaction = Transaction.objects.filter(razorpay_order_id=rzp_order_id).first()
            if not db_transaction:
                logger.warning(f"Transaction registry matching {rzp_order_id} not found in DB.")
                return Response({"status": "ignored"}, status=status.HTTP_200_OK)

            order = db_transaction.order

            if event_type == "payment.captured":
                with transaction.atomic():
                    # Update transaction logs
                    db_transaction.status = Transaction.SUCCESS
                    db_transaction.transaction_id = payment_id
                    db_transaction.raw_response = event_data
                    db_transaction.save(update_fields=["status", "transaction_id", "raw_response", "updated_at"])

                    # Update order payment state
                    order.payment_status = Order.PAYMENT_PAID
                    if order.status == Order.PENDING:
                        order.status = Order.CONFIRMED
                    order.save(update_fields=["payment_status", "status", "updated_at"])
                    
                logger.info(f"Order {order.order_number} confirmed via webhook payment.captured.")

            elif event_type == "payment.failed":
                with transaction.atomic():
                    db_transaction.status = Transaction.FAILED
                    db_transaction.error_message = payment_entity.get("error_description", "Failed payment captured.")
                    db_transaction.raw_response = event_data
                    db_transaction.save(update_fields=["status", "error_message", "raw_response", "updated_at"])

                    order.payment_status = Order.PAYMENT_FAILED
                    order.save(update_fields=["payment_status", "updated_at"])
                    
                logger.warning(f"Order {order.order_number} marked as failed via webhook payment.failed.")

            return Response({"status": "processed"}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("Error processing webhook payload")
            return Response({"error": "Processing error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

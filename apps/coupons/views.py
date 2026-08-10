from decimal import Decimal
from django.utils import timezone
from rest_framework import viewsets, status, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.admin_panel.permissions import AdminPermission
from apps.cart.models import CartItem
from .models import Coupon, CouponUsage
from .serializers import CouponSerializer, CouponValidateSerializer

from django.core.exceptions import ValidationError
from .services import CouponValidationService


def validate_coupon_for_user(coupon, user, subtotal):
    """
    Common business logic for validating a coupon against a user's current order/cart subtotal.
    Returns (is_valid, error_message, discount_amount)
    """
    cart_items = CartItem.objects.filter(user=user)
    service = CouponValidationService()
    try:
        service.validate(coupon, user, cart_items)
        discount_amount = service._calculate_discount(coupon, cart_items, subtotal)
        return True, "", discount_amount
    except ValidationError as e:
        msg = e.message if hasattr(e, 'message') else str(e)
        if hasattr(e, 'message_dict') and e.message_dict:
            msg = next(iter(e.message_dict.values()))[0]
        elif hasattr(e, 'messages') and e.messages:
            msg = e.messages[0]
        return False, msg, Decimal("0.00")


class AdminCouponViewSet(viewsets.ModelViewSet):
    """
    ViewSet for admin to manage coupons (CRUD operations).
    """
    queryset = Coupon.objects.all().order_by("-created_at")
    serializer_class = CouponSerializer
    permission_classes = [AdminPermission]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["is_active", "discount_type"]
    search_fields = ["code", "description"]
    ordering_fields = ["code", "created_at", "end_date"]

    def get_queryset(self):
        queryset = super().get_queryset()
        is_expired = self.request.query_params.get("is_expired")
        if is_expired is not None:
            now = timezone.now()
            if is_expired.lower() == "true":
                queryset = queryset.filter(end_date__lt=now)
            elif is_expired.lower() == "false":
                queryset = queryset.filter(end_date__gte=now)
        return queryset

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        """
        Toggles the active status of a coupon.
        """
        coupon = self.get_object()
        coupon.is_active = not coupon.is_active
        coupon.save()
        serializer = self.get_serializer(coupon)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CouponValidateView(APIView):
    """
    Validates a coupon code and returns the potential discount details.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CouponValidateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": "Invalid data.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        code = serializer.validated_data["code"].upper().strip()
        coupon = Coupon.objects.filter(code__iexact=code).first()
        if not coupon:
            return Response(
                {"success": False, "message": "Invalid coupon code. Coupon does not exist."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get cart items subtotal
        cart_items = CartItem.objects.filter(user=request.user)
        subtotal = sum(item.variant.price * item.quantity for item in cart_items)

        if not cart_items:
            return Response(
                {"success": False, "message": "Your cart is empty."},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_valid, err_msg, discount = validate_coupon_for_user(coupon, request.user, subtotal)
        if not is_valid:
            return Response(
                {"success": False, "message": err_msg},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "success": True,
            "coupon": {
                "id": coupon.id,
                "code": coupon.code,
                "discount_type": coupon.discount_type,
                "discount_value": coupon.discount_value,
                "min_purchase_amount": coupon.min_purchase_amount,
                "max_discount_amount": coupon.max_discount_amount
            },
            "subtotal": subtotal,
            "discount_amount": discount,
            "new_total": subtotal - discount
        }, status=status.HTTP_200_OK)


class CouponApplyView(APIView):
    """
    Applies a coupon code to the current cart. Returns validation details.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # We perform the exact same validation to ensure it can be applied.
        serializer = CouponValidateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": "Invalid data.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        code = serializer.validated_data["code"].upper().strip()
        coupon = Coupon.objects.filter(code__iexact=code).first()
        if not coupon:
            return Response(
                {"success": False, "message": "Invalid coupon code. Coupon does not exist."},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart_items = CartItem.objects.filter(user=request.user)
        subtotal = sum(item.variant.price * item.quantity for item in cart_items)

        if not cart_items:
            return Response(
                {"success": False, "message": "Your cart is empty."},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_valid, err_msg, discount = validate_coupon_for_user(coupon, request.user, subtotal)
        if not is_valid:
            return Response(
                {"success": False, "message": err_msg},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "success": True,
            "message": "Coupon applied successfully.",
            "coupon": {
                "id": coupon.id,
                "code": coupon.code,
                "discount_type": coupon.discount_type,
                "discount_value": coupon.discount_value,
                "min_purchase_amount": coupon.min_purchase_amount,
                "max_discount_amount": coupon.max_discount_amount
            },
            "subtotal": subtotal,
            "discount_amount": discount,
            "new_total": subtotal - discount
        }, status=status.HTTP_200_OK)

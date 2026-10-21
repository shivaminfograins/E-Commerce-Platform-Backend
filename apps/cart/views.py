import uuid

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import CartItem, Guest
from .serializers import CartItemSerializer, CartItemInputSerializer


# ===========================================================
# HELPERS
# ===========================================================

def _cart_queryset():
    """
    Base queryset for cart items optimized to fetch product, variant,
    images, and vendor data in a minimal number of queries.
    """
    return (
        CartItem.objects
        .select_related(
            "vendor_product__product",
            "vendor_product__vendor",
            "vendor_product_variant__product_variant",
        )
        .prefetch_related(
            "vendor_product__product__variants__images",
            "vendor_product_variant__product_variant__images",
        )
    )


def _user_cart(user):
    """Return the optimised queryset scoped to a logged-in user."""
    return _cart_queryset().filter(user=user).order_by("-created_at")


def _guest_cart(guest):
    """Return the optimised queryset scoped to a guest session."""
    return _cart_queryset().filter(guest=guest).order_by("-created_at")


def _get_or_create_guest(request):
    """
    Resolve (or create) a Guest from the incoming request.
    """
    guest_token = (
        request.headers.get("X-Guest-ID")
        or request.COOKIES.get("guest_id")
    )
    guest = None

    if guest_token:
        guest = Guest.objects.filter(guest_token=guest_token).first()

    if not guest:
        guest_token = str(uuid.uuid4())
        guest = Guest.objects.create(guest_token=guest_token)

    return guest, guest_token


def _merge_guest_cart(user, guest_token):
    """
    Move all items from a guest cart into the authenticated user's cart.
    """
    if not guest_token:
        return

    try:
        guest = Guest.objects.get(guest_token=guest_token)
    except Guest.DoesNotExist:
        return

    guest_items = _cart_queryset().filter(guest=guest)

    for item in guest_items:
        if item.vendor_product_variant_id:
            existing = CartItem.objects.filter(
                user=user,
                vendor_product_variant=item.vendor_product_variant,
            ).first()
        else:
            existing = CartItem.objects.filter(
                user=user,
                vendor_product=item.vendor_product,
                vendor_product_variant__isnull=True,
            ).first()

        if existing:
            # Variant already in the user's cart — accumulate quantity.
            existing.quantity += item.quantity
            existing.save(update_fields=["quantity", "updated_at"])
            item.delete()
        else:
            # Transfer ownership: turn the guest item into a user item.
            item.user = user
            item.guest = None
            item.save(update_fields=["user", "guest", "updated_at"])

    # Clean up the guest session
    guest.delete()


def _build_cart_response(items, request, guest_token=None, status_code=status.HTTP_200_OK):
    """
    Shared response builder used by every view.
    """
    serializer = CartItemSerializer(items, many=True, context={"request": request})
    data = serializer.data

    cart_total = round(sum(item["subtotal"] for item in data), 2)

    response_data = {
        "cart_items": data,
        "cart_total": cart_total,
        "item_count": len(data),
    }
    if guest_token:
        response_data["guest_id"] = guest_token

    response = Response(response_data, status=status_code)
    if guest_token:
        response.set_cookie(
            "guest_id",
            guest_token,
            max_age=365 * 24 * 60 * 60,   # 1 year
            httponly=False,
            samesite="Lax",
        )
    return response


# ===========================================================
# VIEWS
# ===========================================================

class CartAPIView(APIView):
    """
    GET  /cart/   → list all items in the caller's cart
    POST /cart/   → add a vendor product offer to the cart (or increment quantity)
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        if request.user.is_authenticated:
            items = _user_cart(request.user)
            return _build_cart_response(items, request)
        else:
            guest, guest_token = _get_or_create_guest(request)
            items = _guest_cart(guest)
            return _build_cart_response(items, request, guest_token=guest_token)

    def post(self, request):
        serializer = CartItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vendor_product = serializer.validated_data.get("vendor_product")
        vendor_product_variant = serializer.validated_data.get("vendor_product_variant")
        quantity = serializer.validated_data["quantity"]

        # Determine stock levels and legacy variant
        if vendor_product_variant:
            stock = vendor_product_variant.stock
            variant = vendor_product_variant.product_variant
        else:
            stock = vendor_product.stock
            variant = None

        if stock <= 0:
            return Response(
                {"detail": "This item is currently out of stock."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        guest_token = None

        # Build query parameters
        kwargs = {}
        if request.user.is_authenticated:
            kwargs["user"] = request.user
        else:
            guest, guest_token = _get_or_create_guest(request)
            kwargs["guest"] = guest

        if vendor_product_variant:
            kwargs["vendor_product_variant"] = vendor_product_variant
        else:
            kwargs["vendor_product"] = vendor_product
            kwargs["vendor_product_variant"] = None

        cart_item, created = CartItem.objects.get_or_create(
            **kwargs,
            defaults={"quantity": quantity, "variant": variant, "vendor_product": vendor_product}
        )

        if not created:
            new_quantity = cart_item.quantity + quantity
            if new_quantity > stock:
                return Response(
                    {
                        "detail": (
                            f"Cannot add {quantity} more unit(s). "
                            f"Only {stock - cart_item.quantity} left in stock."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cart_item.quantity = new_quantity
            cart_item.save(update_fields=["quantity", "updated_at"])
        else:
            if quantity > stock:
                cart_item.delete()
                return Response(
                    {"detail": f"Only {stock} unit(s) available in stock."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if request.user.is_authenticated:
            items = _user_cart(request.user)
        else:
            items = _guest_cart(guest)

        return _build_cart_response(items, request, guest_token=guest_token, status_code=status.HTTP_201_CREATED)


class CartItemDetailAPIView(APIView):
    """
    PATCH  /cart/<cart_item_id>/  → set the quantity of a specific item
    DELETE /cart/<cart_item_id>/  → remove a specific item from the cart
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def _resolve_cart_item(self, request, cart_item_id):
        if request.user.is_authenticated:
            cart_item = get_object_or_404(
                CartItem,
                user=request.user,
                id=cart_item_id,
            )
            return cart_item, None
        else:
            guest_token = (
                request.headers.get("X-Guest-ID")
                or request.COOKIES.get("guest_id")
            )
            if not guest_token:
                error = Response(
                    {"detail": "Guest session not found. Please provide X-Guest-ID header or guest_id cookie."},
                    status=status.HTTP_404_NOT_FOUND,
                )
                return None, error

            cart_item = get_object_or_404(
                CartItem,
                guest__guest_token=guest_token,
                id=cart_item_id,
            )
            return cart_item, guest_token

    def patch(self, request, cart_item_id):
        cart_item, guest_token_or_error = self._resolve_cart_item(request, cart_item_id)
        if cart_item is None:
            return guest_token_or_error

        quantity = request.data.get("quantity")
        try:
            quantity = int(quantity)
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {"detail": "Quantity must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get offer stock
        if cart_item.vendor_product_variant_id:
            stock = cart_item.vendor_product_variant.stock
        else:
            stock = cart_item.vendor_product.stock

        if quantity > stock:
            return Response(
                {"detail": f"Only {stock} unit(s) available in stock."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item.quantity = quantity
        cart_item.save(update_fields=["quantity", "updated_at"])

        guest_token = None if request.user.is_authenticated else guest_token_or_error
        if request.user.is_authenticated:
            items = _user_cart(request.user)
        else:
            items = _guest_cart(cart_item.guest)

        return _build_cart_response(items, request, guest_token=guest_token)

    def delete(self, request, cart_item_id):
        cart_item, guest_token_or_error = self._resolve_cart_item(request, cart_item_id)
        if cart_item is None:
            return guest_token_or_error

        owner_user  = cart_item.user
        owner_guest = cart_item.guest
        guest_token = None if request.user.is_authenticated else guest_token_or_error

        cart_item.delete()

        if request.user.is_authenticated:
            items = _user_cart(owner_user)
        else:
            items = _guest_cart(owner_guest)

        response = _build_cart_response(items, request, guest_token=guest_token)
        response.data["message"] = "Item removed from cart."
        return response


class CartClearAPIView(APIView):
    """
    DELETE /cart/clear/  → remove ALL items from the caller's cart.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def delete(self, request):
        if request.user.is_authenticated:
            CartItem.objects.filter(user=request.user).delete()
            return Response(
                {"message": "Cart cleared.", "cart_items": [], "cart_total": 0.00, "item_count": 0},
                status=status.HTTP_200_OK,
            )
        else:
            guest_token = (
                request.headers.get("X-Guest-ID")
                or request.COOKIES.get("guest_id")
            )
            if guest_token:
                CartItem.objects.filter(guest__guest_token=guest_token).delete()
            return Response(
                {"message": "Cart cleared.", "cart_items": [], "cart_total": 0.00, "item_count": 0},
                status=status.HTTP_200_OK,
            )


class CartMergeAPIView(APIView):
    """
    POST /cart/merge/  → merge a guest cart into the authenticated user's cart.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        guest_token = (
            request.data.get("guest_id")
            or request.headers.get("X-Guest-ID")
            or request.COOKIES.get("guest_id")
        )
        if not guest_token:
            return Response(
                {"detail": "Provide guest_id in the request body, X-Guest-ID header, or guest_id cookie."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _merge_guest_cart(request.user, guest_token)

        items = _user_cart(request.user)
        response = _build_cart_response(items, request)
        response.data["message"] = "Cart merged successfully."
        response.delete_cookie("guest_id")
        return response

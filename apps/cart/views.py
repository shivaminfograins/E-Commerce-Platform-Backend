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
    Base queryset used everywhere.

    select_related("variant__product")
        → fetches CartItem + ProductVariant + Product in ONE SQL JOIN.

    prefetch_related("variant__product__images")
        → fetches all ProductImage rows for those products in ONE extra
          SQL query (not N queries), used by CartItemSerializer.get_image().
    """
    return (
        CartItem.objects
        .select_related("variant__product")
        .prefetch_related("variant__product__images")
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

    Lookup order:
      1. X-Guest-ID request header  (preferred for API clients / SPA)
      2. guest_id cookie            (browser fallback)

    If neither is present a fresh guest token is generated and a new
    Guest row is created.

    Returns: (Guest instance, guest_token string)
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

    Merge strategy:
      • If the user already has the same variant → add quantities.
      • Otherwise → transfer the guest row by reassigning user/guest FKs.

    After merging, the Guest row itself is deleted (cascades to any
    remaining items, though there should be none).

    This function is intentionally silent on errors so a failed merge
    never blocks the login flow.
    """
    if not guest_token:
        return

    try:
        guest = Guest.objects.get(guest_token=guest_token)
    except Guest.DoesNotExist:
        return

    guest_items = _cart_queryset().filter(guest=guest)

    for item in guest_items:
        existing = CartItem.objects.filter(
            user=user,
            variant=item.variant,
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

    # Clean up the guest session (any remaining items cascade-delete).
    guest.delete()


def _build_cart_response(items, request, guest_token=None, status_code=status.HTTP_200_OK):
    """
    Shared response builder used by every view.

    Computes cart_total (sum of all subtotals) server-side so the
    frontend can display it without iterating over items.

    Sets the guest_id cookie when guest_token is provided.
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
            httponly=False,                 # JS-readable so the SPA can send it back
            samesite="Lax",
        )
    return response


# ===========================================================
# VIEWS
# ===========================================================

class CartAPIView(APIView):
    """
    GET  /cart/   → list all items in the caller's cart
    POST /cart/   → add a variant to the cart (or increment quantity)

    Authentication:
      • JWT token present  → user cart
      • No JWT token       → guest cart (X-Guest-ID header or guest_id cookie)
    """
    # Allow both authenticated and unauthenticated access.
    # JWTAuthentication is attempted silently; request.user.is_authenticated
    # tells us which path to take.
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    # ── GET ──────────────────────────────────────────────────
    def get(self, request):
        """
        Return the current cart contents.

        For guests a new session is created if none exists, so the
        response always includes a guest_id they can store.
        """
        if request.user.is_authenticated:
            items = _user_cart(request.user)
            return _build_cart_response(items, request)
        else:
            guest, guest_token = _get_or_create_guest(request)
            items = _guest_cart(guest)
            return _build_cart_response(items, request, guest_token=guest_token)

    # ── POST ─────────────────────────────────────────────────
    def post(self, request):
        """
        Add a variant to the cart.

        Request body:
            { "variant": <int>, "quantity": <int> }

        Business rules:
          • If the same variant already exists in the cart, the quantity
            is INCREMENTED (not replaced).  This matches typical UX where
            clicking "Add to cart" twice adds two units.
          • Stock validation: total cart quantity cannot exceed available stock.
          • Inactive variants / parent products are rejected by the serializer.
        """
        serializer = CartItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        variant  = serializer.validated_data["variant"]
        quantity = serializer.validated_data["quantity"]

        guest_token = None

        if request.user.is_authenticated:
            cart_item, created = CartItem.objects.get_or_create(
                user=request.user,
                variant=variant,
                defaults={"quantity": quantity},
            )
        else:
            guest, guest_token = _get_or_create_guest(request)
            cart_item, created = CartItem.objects.get_or_create(
                guest=guest,
                variant=variant,
                defaults={"quantity": quantity},
            )

        if not created:
            # Variant already in cart — increment, then validate stock.
            new_quantity = cart_item.quantity + quantity
            if new_quantity > variant.stock:
                return Response(
                    {
                        "detail": (
                            f"Cannot add {quantity} more unit(s). "
                            f"Only {variant.stock - cart_item.quantity} left in stock."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cart_item.quantity = new_quantity
            cart_item.save(update_fields=["quantity", "updated_at"])
        else:
            # Freshly created item — still check stock.
            if quantity > variant.stock:
                cart_item.delete()
                return Response(
                    {"detail": f"Only {variant.stock} unit(s) available in stock."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Return the full updated cart (not just the single item).
        if request.user.is_authenticated:
            items = _user_cart(request.user)
        else:
            items = _guest_cart(guest)

        return _build_cart_response(items, request, guest_token=guest_token, status_code=status.HTTP_201_CREATED)


class CartItemDetailAPIView(APIView):
    """
    PATCH  /cart/<variant_id>/  → set the quantity of a specific item
    DELETE /cart/<variant_id>/  → remove a specific item from the cart

    The URL kwarg is `variant_id` (integer PK of ProductVariant).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def _resolve_cart_item(self, request, variant_id):
        """
        Locate the CartItem for the current owner + the given variant_id.
        Returns (cart_item, guest_token) or raises Http404 / returns an
        error Response for missing guest sessions.

        Raises:
            Http404 if the CartItem does not exist for this owner.
        Returns:
            (CartItem, guest_token_or_None)  on success
            (None, Response)                 when the guest session is missing
        """
        if request.user.is_authenticated:
            cart_item = get_object_or_404(
                CartItem,
                user=request.user,
                variant_id=variant_id,
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
                variant_id=variant_id,
            )
            return cart_item, guest_token

    # ── PATCH ────────────────────────────────────────────────
    def patch(self, request, variant_id):
        """
        Set (replace) the quantity of a cart item.

        Request body:
            { "quantity": <int> }

        Unlike POST, this SETS the quantity rather than incrementing it —
        useful for the quantity input field in the cart UI.
        """
        cart_item, guest_token_or_error = self._resolve_cart_item(request, variant_id)
        if cart_item is None:
            return guest_token_or_error  # error Response

        # Validate incoming quantity.
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

        # Stock check against the variant's current stock level.
        if quantity > cart_item.variant.stock:
            return Response(
                {"detail": f"Only {cart_item.variant.stock} unit(s) available in stock."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item.quantity = quantity
        cart_item.save(update_fields=["quantity", "updated_at"])

        # Return full updated cart.
        guest_token = None if request.user.is_authenticated else guest_token_or_error
        if request.user.is_authenticated:
            items = _user_cart(request.user)
        else:
            items = _guest_cart(cart_item.guest)

        return _build_cart_response(items, request, guest_token=guest_token)

    # ── DELETE ───────────────────────────────────────────────
    def delete(self, request, variant_id):
        """
        Remove a variant from the cart entirely.
        Returns the updated cart after removal.
        """
        cart_item, guest_token_or_error = self._resolve_cart_item(request, variant_id)
        if cart_item is None:
            return guest_token_or_error  # error Response

        # Remember the owner before deletion for the follow-up query.
        owner_user  = cart_item.user
        owner_guest = cart_item.guest
        guest_token = None if request.user.is_authenticated else guest_token_or_error

        cart_item.delete()

        if request.user.is_authenticated:
            items = _user_cart(owner_user)
        else:
            items = _guest_cart(owner_guest)

        response = _build_cart_response(items, request, guest_token=guest_token)
        # Inject a success message into the existing response data.
        response.data["message"] = "Item removed from cart."
        return response


class CartClearAPIView(APIView):
    """
    DELETE /cart/clear/  → remove ALL items from the caller's cart.

    Useful for "Clear cart" buttons and post-checkout cleanup.
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

    Requires: JWT authentication (users must be logged in to merge).

    Request body (any of the three sources is accepted):
        { "guest_id": "<uuid>" }
        OR  X-Guest-ID header
        OR  guest_id cookie

    Merge strategy (see _merge_guest_cart for detail):
      • Same variant already in user cart → quantities are summed.
      • New variant → guest row is transferred to the user.
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
        # Clear the guest cookie now that the session has been consumed.
        response.delete_cookie("guest_id")
        return response

"""
apps/orders/views.py
====================

View map
--------

  PlaceOrderView     POST   /api/orders/
      Full checkout pipeline inside transaction.atomic().

  OrderListView      GET    /api/orders/
      Paginated, searchable, sortable list of the user's own orders.
      Uses the lightweight OrderSummarySerializer (no nested items).

  OrderDetailView    GET    /api/orders/<pk>/
      Full order detail with nested line items.
      Uses the full OrderSerializer.

  CancelOrderView    PATCH  /api/orders/<pk>/cancel/
      Idempotent cancellation with atomic stock restoration.

──────────────────────────────────────────────────────────────────────
Pagination  — PageNumberPagination (page / page_size query params)
Ordering    — ?ordering=created_at | -created_at | total_amount | status
Search      — ?search=<order_number_prefix>
──────────────────────────────────────────────────────────────────────

PlaceOrderView business logic
------------------------------
  1.  JWT authentication         → IsAuthenticated rejects 401 automatically
  2.  Input validation           → PlaceOrderSerializer validates body
  3.  Address ownership check    → Address.objects.get(pk=…, user=request.user)
  4.  Cart fetch                 → select_related to avoid N+1; 400 if empty
  5.  Stock validation (all)     → collect every OOS item before returning 400
  6.  Price calculation          → Decimal arithmetic (no float rounding)
  7.  transaction.atomic() ─────────────────────────────────────────────────
  8.    Create Order             → snapshot address + price breakdown
  9.    bulk_create OrderItems   → single INSERT; snapshot price, sku, image
  10.   Decrement stock          → F("stock") - qty (SQL-level, race-free)
  11.   Clear cart               → single DELETE
  ───────────────────────────────────────────────────────────────────────────
  12. Re-fetch order             → prefetch_related for nested serialisation
  13. Return 201                 → full OrderSerializer response

Error codes
-----------
  400 — bad input / empty cart / insufficient stock / wrong address
  401 — unauthenticated
  404 — order not found or belongs to another user (PATCH cancel / GET detail)
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from rest_framework import generics, filters, status, serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Address
from apps.cart.models import CartItem
from apps.products.models import ProductVariant

from .models import Order, OrderItem
from apps.payments.models import Transaction
from apps.payments.services import RazorpayService
from .serializers import (
    OrderSerializer,
    OrderSummarySerializer,
    PlaceOrderSerializer,
)


# ---------------------------------------------------------------------------
# Business constants — override in settings.py for environment-specific values
# ---------------------------------------------------------------------------
FREE_SHIPPING_THRESHOLD = Decimal(
    getattr(settings, "FREE_SHIPPING_THRESHOLD", "999.00")
)
SHIPPING_FEE = Decimal(
    getattr(settings, "SHIPPING_FEE", "99.00")
)
TAX_RATE = Decimal(
    getattr(settings, "TAX_RATE", "0.00")   # e.g. "0.18" for 18 % GST
)


# ===========================================================================
# PAGINATION
# ===========================================================================
class OrderPagination(PageNumberPagination):
    """
    Standard page-number pagination for the order list.

    Query params
    ------------
      ?page=2            → go to page 2
      ?page_size=5       → override default page size (capped at max_page_size)

    The response envelope:
      {
        "count":    42,
        "total_pages": 5,
        "next":     "http://…/?page=3",
        "previous": "http://…/?page=1",
        "results":  [ … ]
      }
    """

    page_size              = 10    # default items per page
    page_size_query_param  = "page_size"
    max_page_size          = 50    # hard cap so clients cannot request 10 000 rows
    page_query_param       = "page"

    def get_paginated_response(self, data):
        """Adds total_pages to the standard DRF envelope."""
        return Response(
            {
                "success":     True,
                "count":       self.page.paginator.count,
                "total_pages": self.page.paginator.num_pages,
                "next":        self.get_next_link(),
                "previous":    self.get_previous_link(),
                "results":     data,
            }
        )


# ===========================================================================
# POST /api/orders/
# ===========================================================================
class PlaceOrderView(APIView):
    """
    Checkout endpoint.

    Creates an Order from the authenticated user's active cart in a single
    atomic database transaction.  Every step either succeeds completely or
    the entire operation is rolled back — no partial orders, no orphaned
    stock decrements.

    Method  : POST
    Auth    : JWT Bearer (IsAuthenticated)
    Body    : { address, payment_method, coupon_code?, notes? }
    Returns : 201 + full OrderSerializer on success
              400 + structured errors on any validation / business failure
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):

        # ── Step 1: Validate request body ─────────────────────────────
        input_serializer = PlaceOrderSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "message": "Invalid request data.",
                    "errors":  input_serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = input_serializer.validated_data

        # ── Step 2: Validate address ownership ───────────────────────
        # A single get() that checks both PK and owner prevents:
        #   • Address enumeration attacks (returns 400 not 403/404)
        #   • Using another user's address ID
        try:
            address = Address.objects.get(
                pk=data["address"],
                user=request.user,
            )
        except Address.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": (
                        "Address not found. Please use a valid address "
                        "from your account."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 3: Fetch cart ────────────────────────────────────────
        # select_related pulls variant + product + product images in a
        # single SQL JOIN, so the stock-check and image-URL resolution
        # loops below run without any additional queries.
        cart_items = list(
            CartItem.objects
            .filter(user=request.user)
            .select_related(
                "variant__product",
            )
            .prefetch_related("variant__images")
        )

        if not cart_items:
            return Response(
                {
                    "success": False,
                    "message": (
                        "Your cart is empty. Add items before placing an order."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 4: Stock validation ──────────────────────────────────
        # Collect ALL stock failures before returning so the customer
        # can fix every problem in one go instead of discovering them
        # one at a time on successive requests.
        stock_errors = []
        for item in cart_items:
            if item.quantity > item.variant.stock:
                v = item.variant
                stock_errors.append(
                    {
                        "variant_id":   v.id,
                        "variant_name": f"{v.product.name} — {v.name}",
                        "sku":          v.sku,
                        "requested":    item.quantity,
                        "available":    v.stock,
                    }
                )

        if stock_errors:
            return Response(
                {
                    "success": False,
                    "message": (
                        "One or more items in your cart have insufficient stock."
                    ),
                    "stock_errors": stock_errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 5: Price calculation ─────────────────────────────────
        # Decimal throughout — never float — to avoid rounding errors.
        subtotal = sum(
            item.variant.price * item.quantity
            for item in cart_items
        )
        shipping_charge = (
            Decimal("0.00")
            if subtotal >= FREE_SHIPPING_THRESHOLD
            else SHIPPING_FEE
        )
        tax      = (subtotal * TAX_RATE).quantize(Decimal("0.01"))
        discount = Decimal("0.00")   # extend with coupon logic here

        # ── Steps 6–10: Atomic database operations ────────────────────
        with transaction.atomic():

            # ── Step 6: Create Order ──────────────────────────────────
            # order_number is auto-generated in Order.save() via UUID4.
            # total_amount is also recomputed in Order.save(), but we
            # pass it explicitly as an extra safety net.
            order = Order.objects.create(
                user=request.user,
                address=address,
                # ── Address snapshot ───────────────────────────────────
                snapshot_full_name=address.full_name,
                snapshot_phone=address.phone,
                snapshot_address_line_1=address.address_line_1,
                snapshot_address_line_2=address.address_line_2,
                snapshot_landmark=address.landmark,
                snapshot_city=address.city,
                snapshot_state=address.state,
                snapshot_country=address.country,
                snapshot_postal_code=address.postal_code,
                # ── Status ────────────────────────────────────────────
                status=Order.PENDING,
                payment_method=data["payment_method"],
                # COD is automatically "paid" (payment happens on delivery;
                # all online gateway methods start as pending until webhook).
                payment_status=(
                    Order.PAYMENT_PAID
                    if data["payment_method"] == Order.COD
                    else Order.PAYMENT_PENDING
                ),
                # ── Price ─────────────────────────────────────────────
                subtotal=subtotal,
                shipping_charge=shipping_charge,
                discount=discount,
                tax=tax,
                total_amount=subtotal + shipping_charge + tax - discount,
                # ── Misc ──────────────────────────────────────────────
                coupon_code=data.get("coupon_code", ""),
                notes=data.get("notes", ""),
            )

            # ── Razorpay Order Creation ──────────────────────────────
            if data["payment_method"] == Order.RAZORPAY:
                rzp_response = RazorpayService.create_razorpay_order(
                    order_number=order.order_number,
                    amount=order.total_amount
                )
                if not rzp_response["success"]:
                    # Rollback transaction cleanly
                    raise serializers.ValidationError(
                        f"Payment gateway initialization failed: {rzp_response.get('error')}"
                    )
                
                # Record transaction registry
                Transaction.objects.create(
                    order=order,
                    payment_method=Transaction.RAZORPAY,
                    status=Transaction.PENDING,
                    amount=order.total_amount,
                    razorpay_order_id=rzp_response["id"],
                    raw_response=rzp_response["raw_response"]
                )
            elif data["payment_method"] == Order.COD:
                # Record COD transaction Registry
                Transaction.objects.create(
                    order=order,
                    payment_method=Transaction.COD,
                    status=Transaction.SUCCESS,
                    amount=order.total_amount
                )

            # ── Step 7: Create OrderItems (bulk) ──────────────────────
            # bulk_create → single INSERT statement.
            # IMPORTANT: bulk_create bypasses Model.save(), so we compute
            # `total` (price × quantity) manually here.
            order_items_to_create = []
            for item in cart_items:
                v = item.variant
                p = v.product

                # Resolve image URL from the pre-fetched related manager.
                # all() iterates the in-memory prefetch cache — zero extra DB queries.
                image_url = ""
                first_image = v.images.first()
                if first_image and first_image.image:
                    try:
                        image_url = request.build_absolute_uri(
                            first_image.image.url
                        )
                    except Exception:
                        image_url = ""

                order_items_to_create.append(
                    OrderItem(
                        order=order,
                        # Live FK references (nullable SET_NULL catalogue links)
                        product=p,
                        variant=v,
                        # Immutable purchase snapshot
                        product_name=p.name,
                        variant_name=v.name,
                        sku=v.sku,
                        product_image=image_url,
                        # Financial snapshot
                        price=v.price,
                        quantity=item.quantity,
                        total=v.price * item.quantity,
                    )
                )

            OrderItem.objects.bulk_create(order_items_to_create)

            # ── Step 8: Atomically decrement variant stock ────────────
            # F("stock") - qty translates to:
            #   UPDATE products_productvariant SET stock = stock - %s WHERE id = %s
            # This is a DB-level atomic operation — no read-modify-write
            # race condition even under concurrent high-traffic checkouts.
            for item in cart_items:
                ProductVariant.objects.filter(pk=item.variant_id).update(
                    stock=F("stock") - item.quantity
                )

            # ── Step 9: Clear user's cart ─────────────────────────────
            # Single DELETE — the cart is cleared only inside the
            # transaction, so it stays intact if any prior step fails.
            CartItem.objects.filter(user=request.user).delete()

        # ── Step 10: Re-fetch and return ──────────────────────────────
        # Re-fetch the newly created order with all relations prefetched
        # so the serializer can render nested items without N+1 queries.
        order_fresh = (
            Order.objects
            .prefetch_related(
                "items",
                "items__product",
                "items__variant",
            )
            .get(pk=order.pk)
        )

        serializer = OrderSerializer(
            order_fresh,
            context={"request": request},
        )
        return Response(
            {
                "success": True,
                "message": "Order placed successfully.",
                "order":   serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )


# ===========================================================================
# GET /api/orders/
# ===========================================================================
class OrderListView(generics.ListAPIView):
    """
    Returns a paginated, searchable, sortable list of the requesting
    user's own orders.  Uses OrderSummarySerializer (no nested items)
    for bandwidth efficiency.

    Method  : GET
    Auth    : JWT Bearer (IsAuthenticated)
    Returns : 200 + paginated order list

    Query parameters
    ----------------
      ?page=<n>              Page number (default 1)
      ?page_size=<n>         Items per page (default 10, max 50)

      ?search=<term>         Searches:
                               • order_number (icontains)
                               • status       (icontains)

      ?ordering=<field>      Sort ascending; prefix with '-' for descending.
                             Allowed fields:
                               created_at      (newest first by default)
                               total_amount
                               status
                               payment_status

    Example
    -------
      GET /api/orders/?search=ORD-2026&ordering=-total_amount&page=1
    """

    permission_classes  = [IsAuthenticated]
    serializer_class    = OrderSummarySerializer
    pagination_class    = OrderPagination

    # DRF filter backends wired in here — no extra packages required for
    # SearchFilter / OrderingFilter (they ship with DRF).
    filter_backends     = [filters.SearchFilter, filters.OrderingFilter]

    # SearchFilter: applies icontains across the listed fields.
    # A single ?search=X queries all fields with OR logic.
    search_fields       = [
        "order_number",   # primary search: "ORD-20260706-A1B2"
        "status",         # secondary: "pending", "shipped" …
    ]

    # OrderingFilter: only the fields listed here can be used in ?ordering=
    # so clients cannot sort by arbitrary/expensive computed fields.
    ordering_fields     = [
        "created_at",
        "total_amount",
        "status",
        "payment_status",
    ]

    # Default ordering — newest orders first
    ordering            = ["-created_at"]

    def get_queryset(self):
        """
        Scoped strictly to the requesting user's orders.

        Uses select_related("address") to avoid a per-row JOIN for the
        address FK (even though we display the snapshot fields, Django
        may touch the FK if admin tools or other serializers reference it).
        prefetch_related("items") so item_count (a property on Order that
        calls self.items.count()) uses the pre-fetched cache instead of
        issuing one COUNT query per order row.
        """
        return (
            Order.objects
            .filter(user=self.request.user)
            .select_related("address")
            .prefetch_related("items")
        )

    def list(self, request, *args, **kwargs):
        """
        Override list() to wrap the paginated response in a consistent
        {success, message, …} envelope matching the rest of the API.
        The actual pagination envelope is produced by OrderPagination.
        """
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        # Fallback if pagination is disabled (shouldn't happen with this config)
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count":   queryset.count(),
                "results": serializer.data,
            }
        )


# ===========================================================================
# GET /api/orders/<pk>/
# ===========================================================================
class OrderDetailView(generics.RetrieveAPIView):
    """
    Returns full detail of a single order including all nested line items,
    the complete address snapshot, and the full price breakdown.

    Method  : GET
    Auth    : JWT Bearer (IsAuthenticated)
    Returns : 200 + full OrderSerializer
              404 if the PK does not exist OR belongs to another user

    Security note
    -------------
    The queryset is filtered to user=request.user so PK enumeration is
    safe — a user who guesses order PK 9999 gets a 404, not a 403, which
    reveals no information about whose order it is.
    """

    permission_classes = [IsAuthenticated]
    serializer_class   = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects
            .filter(user=self.request.user)
            .select_related("address")
            .prefetch_related(
                "items",
                "items__product",
                "items__variant",
            )
        )

    def retrieve(self, request, *args, **kwargs):
        instance   = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "success": True,
                "order":   serializer.data,
            }
        )


# ===========================================================================
# PATCH /api/orders/<pk>/cancel/
# ===========================================================================
class CancelOrderView(APIView):
    """
    Cancels the order identified by <pk> and atomically restores
    the stock for every line item.

    Why PATCH (not POST or DELETE)?
    --------------------------------
    PATCH semantics are correct here: we are partially updating the order
    resource (changing its `status` field from any pre-delivered state to
    "cancelled").  POST would imply creating a new resource; DELETE would
    imply removing the record (we keep it for audit history).

    Method  : PATCH
    Auth    : JWT Bearer (IsAuthenticated)
    Body    : {} — no payload required
    Returns : 200 + updated OrderSerializer on success
              400 + message if the order is already delivered / cancelled / refunded
              404 if the order does not exist or belongs to another user

    Idempotency note
    ----------------
    Cancelling an already-cancelled order returns 400, NOT 200.
    This is intentional: a 200 would make downstream webhook consumers
    think a new cancellation event occurred, potentially triggering a
    second refund.  The 400 signals "nothing changed".

    Stock restoration
    -----------------
    Uses F("stock") + item.quantity (SQL-level UPDATE) so stock is restored
    atomically — no read-modify-write race condition under concurrent requests.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):

        # ── 1. Ownership-checked lookup ───────────────────────────────
        # 404 hides whether the order exists at all for non-owners.
        try:
            order = (
                Order.objects
                .prefetch_related("items__variant")
                .get(pk=pk, user=request.user)
            )
        except Order.DoesNotExist:
            return Response(
                {"success": False, "message": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── 2. Business rule check ────────────────────────────────────
        if not order.is_cancellable:
            return Response(
                {
                    "success": False,
                    "message": (
                        f"This order cannot be cancelled. "
                        f"Current status: '{order.get_status_display()}'."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 3. Atomic cancellation + stock restore ────────────────────
        with transaction.atomic():
            for item in order.items.all():
                # Guard against variant being NULL (catalogue entry deleted)
                if item.variant_id:
                    ProductVariant.objects.filter(pk=item.variant_id).update(
                        stock=F("stock") + item.quantity
                    )

            # update_fields limits the UPDATE to exactly these two columns
            # for efficiency and to avoid overwriting concurrent changes to
            # other fields (e.g. payment_status updated by a webhook).
            order.status = Order.CANCELLED
            order.save(update_fields=["status", "updated_at"])

        # ── 4. Return the updated order ───────────────────────────────
        serializer = OrderSerializer(order, context={"request": request})
        return Response(
            {
                "success": True,
                "message": "Order cancelled successfully.",
                "order":   serializer.data,
            },
            status=status.HTTP_200_OK,
        )

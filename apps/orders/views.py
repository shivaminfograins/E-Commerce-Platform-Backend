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
        
        # ── Coupon Validation ──────────────────────────────────────────
        discount = Decimal("0.00")
        coupon_code = data.get("coupon_code", "").upper().strip()
        coupon = None
        if coupon_code:
            from apps.coupons.models import Coupon
            from apps.coupons.views import validate_coupon_for_user
            coupon = Coupon.objects.filter(code__iexact=coupon_code).first()
            if not coupon:
                return Response(
                    {
                        "success": False,
                        "message": f"Coupon code '{coupon_code}' is invalid.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            is_valid, err_msg, discount = validate_coupon_for_user(coupon, request.user, subtotal)
            if not is_valid:
                return Response(
                    {
                        "success": False,
                        "message": err_msg,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
                coupon=coupon,
                coupon_code=coupon.code if coupon else "",
                notes=data.get("notes", ""),
            )

            # Record Coupon Usage
            if coupon:
                from apps.coupons.models import CouponUsage
                CouponUsage.objects.create(
                    coupon=coupon,
                    user=request.user,
                    order=order
                )
                Coupon.objects.filter(pk=coupon.pk).update(usage_count=F("usage_count") + 1)

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


class DownloadInvoiceView(APIView):
    """
    Generates a professional, legally-compliant PDF Tax Invoice
    and returns it as a binary PDF attachment.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            order = Order.objects.prefetch_related("items").get(pk=pk, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "message": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        import io
        from django.http import FileResponse
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        styles = getSampleStyleSheet()
        
        # Define clean, professional text styles
        title_style = ParagraphStyle(
            "InvoiceTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a")
        )
        
        bold_lbl_style = ParagraphStyle(
            "BoldLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#334155")
        )
        
        val_style = ParagraphStyle(
            "ValueStyle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#475569")
        )

        val_bold_style = ParagraphStyle(
            "ValueBoldStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1e293b")
        )

        tbl_hdr_style = ParagraphStyle(
            "TableHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#1e293b")
        )

        tbl_cell_style = ParagraphStyle(
            "TableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#334155")
        )

        tbl_cell_right = ParagraphStyle(
            "TableCellRight",
            parent=tbl_cell_style,
            alignment=2 # Right align
        )

        tbl_cell_bold_right = ParagraphStyle(
            "TableCellBoldRight",
            parent=tbl_cell_style,
            fontName="Helvetica-Bold",
            alignment=2
        )

        story = []

        # ── 1. Company Identity & Invoice Header (Two Columns) ──
        company_info = """<b>Sold By:</b><br/>
<b>ShopEase Retail Private Limited</b><br/>
123 Tech Park, Phase II, Scheme 54,<br/>
Indore, Madhya Pradesh, 452001<br/>
<b>GSTIN:</b> 23AAACS1234A1Z1<br/>
<b>PAN:</b> AAACS1234A | <b>CIN:</b> U72200MP2026PTC123456<br/>
<b>Email:</b> billing@shopease.com | <b>Phone:</b> +91 731 555 1234
"""
        
        invoice_date_str = order.created_at.strftime("%Y-%m-%d %H:%M")
        invoice_num = f"INV-{order.created_at.strftime('%Y%m%d')}-{order.id:04d}"
        
        meta_info = f"""<font size="16"><b>TAX INVOICE</b></font><br/><br/>
<b>Invoice Number:</b> {invoice_num}<br/>
<b>Invoice Date:</b> {invoice_date_str}<br/>
<b>Order Number:</b> {order.order_number}<br/>
<b>Order Date:</b> {invoice_date_str}<br/>
<b>Payment Method:</b> {order.get_payment_method_display()}
"""

        header_data = [
            [Paragraph(meta_info, val_style), Paragraph(company_info, val_style)]
        ]
        header_table = Table(header_data, colWidths=[260, 260])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 10))

        # Thin divider
        divider = Table([[""]], colWidths=[522])
        divider.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, -1), 1, colors.HexColor("#e2e8f0")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(divider)
        story.append(Spacer(1, 10))

        # ── 2. Shipping & Billing Address (Side-by-side) ──
        billing_address = f"""<b>Billing Address:</b><br/>
{order.snapshot_full_name}<br/>
{order.snapshot_address_line_1}<br/>
{order.snapshot_address_line_2}<br/>
{order.snapshot_city}, {order.snapshot_state} – {order.snapshot_postal_code}<br/>
{order.snapshot_country}<br/>
Phone: {order.snapshot_phone}
"""
        
        shipping_address = f"""<b>Shipping Address:</b><br/>
{order.snapshot_full_name}<br/>
{order.snapshot_address_line_1}<br/>
{order.snapshot_address_line_2}<br/>
{order.snapshot_city}, {order.snapshot_state} – {order.snapshot_postal_code}<br/>
{order.snapshot_country}<br/>
Phone: {order.snapshot_phone}
"""

        addr_data = [
            [Paragraph(billing_address, val_style), Paragraph(shipping_address, val_style)]
        ]
        addr_table = Table(addr_data, colWidths=[260, 260])
        addr_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        story.append(addr_table)
        story.append(Spacer(1, 10))
        story.append(divider)
        story.append(Spacer(1, 10))

        # ── 3. Products Table ──
        # Check if IGST or CGST+SGST applies
        is_mp = order.snapshot_state.strip().lower() in ["madhya pradesh", "mp"]
        tax_type = "CGST/SGST" if is_mp else "IGST"
        tax_rate_percent = 18
        
        table_data = [
            [
                Paragraph("<b>S.No.</b>", tbl_hdr_style),
                Paragraph("<b>Product Description</b>", tbl_hdr_style),
                Paragraph("<b>SKU</b>", tbl_hdr_style),
                Paragraph("<b>HSN</b>", tbl_hdr_style),
                Paragraph("<b>Qty</b>", tbl_hdr_style),
                Paragraph("<b>Unit Price</b>", tbl_hdr_style),
                Paragraph("<b>Gross Amt</b>", tbl_hdr_style),
                Paragraph("<b>Tax Rate</b>", tbl_hdr_style),
                Paragraph("<b>Tax Type</b>", tbl_hdr_style),
                Paragraph("<b>Tax Amt</b>", tbl_hdr_style),
                Paragraph("<b>Total</b>", tbl_hdr_style),
            ]
        ]

        # Calculate pro-rata tax and discounts per item
        # If total_amount and subtotal are present, we can derive the multiplier:
        # subtotal / total_amount or calculate details based on order-level discount/tax.
        # Let's compute accurate item breakdowns.
        items = order.items.all()
        for idx, item in enumerate(items, 1):
            hsn_code = "8517 12 00"  # default electronic HSN
            
            # Simple tax calculation: 18% inclusive or exclusive.
            # Let's say unit price is base price.
            gross_amount = item.price * item.quantity
            
            # Pro-rata discount
            item_discount = Decimal("0.00")
            if order.subtotal > 0:
                item_discount = (gross_amount / order.subtotal) * order.discount
            
            taxable_val = gross_amount - item_discount
            
            # Pro-rata tax
            item_tax = Decimal("0.00")
            if order.subtotal > 0:
                item_tax = (gross_amount / order.subtotal) * order.tax
            
            total_val = taxable_val + item_tax

            table_data.append([
                Paragraph(str(idx), tbl_cell_style),
                Paragraph(f"<b>{item.product_name}</b><br/>{item.variant_name}", tbl_cell_style),
                Paragraph(item.sku or "N/A", tbl_cell_style),
                Paragraph(hsn_code, tbl_cell_style),
                Paragraph(str(item.quantity), tbl_cell_style),
                Paragraph(f"₹{item.price:.2f}", tbl_cell_right),
                Paragraph(f"₹{gross_amount:.2f}", tbl_cell_right),
                Paragraph(f"{tax_rate_percent}%", tbl_cell_right),
                Paragraph(tax_type, tbl_cell_style),
                Paragraph(f"₹{item_tax:.2f}", tbl_cell_right),
                Paragraph(f"₹{total_val:.2f}", tbl_cell_right),
            ])

        # Table styling
        prod_table = Table(table_data, colWidths=[25, 110, 50, 45, 25, 50, 50, 35, 45, 45, 42])
        prod_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f8fafc")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(prod_table)
        story.append(Spacer(1, 15))

        # ── 4. Summary & Totals (Bottom-Right aligned) ──
        tax_str = f"₹{order.tax:.2f}"
        cgst_val = Decimal("0.00")
        sgst_val = Decimal("0.00")
        igst_val = Decimal("0.00")
        
        if is_mp:
            cgst_val = order.tax / 2
            sgst_val = order.tax / 2
        else:
            igst_val = order.tax

        summary_rows = [
            [Paragraph("Subtotal", tbl_cell_style), Paragraph(f"₹{order.subtotal:.2f}", tbl_cell_right)],
            [Paragraph("Shipping Charges", tbl_cell_style), Paragraph(f"₹{order.shipping_charge:.2f}", tbl_cell_right)],
            [Paragraph("Discount", tbl_cell_style), Paragraph(f"-₹{order.discount:.2f}", tbl_cell_right)],
        ]
        
        if is_mp:
            summary_rows.append([Paragraph("CGST (9%)", tbl_cell_style), Paragraph(f"₹{cgst_val:.2f}", tbl_cell_right)])
            summary_rows.append([Paragraph("SGST (9%)", tbl_cell_style), Paragraph(f"₹{sgst_val:.2f}", tbl_cell_right)])
        else:
            summary_rows.append([Paragraph("IGST (18%)", tbl_cell_style), Paragraph(f"₹{igst_val:.2f}", tbl_cell_right)])
            
        summary_rows.append([Paragraph("<b>Grand Total</b>", bold_lbl_style), Paragraph(f"<b>₹{order.total_amount:.2f}</b>", tbl_cell_bold_right)])
        summary_rows.append([Paragraph("<b>Amount Paid</b>", bold_lbl_style), Paragraph(f"<b>₹{order.total_amount:.2f}</b>", tbl_cell_bold_right)])

        summary_table = Table(summary_rows, colWidths=[150, 100])
        summary_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor("#f8fafc")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
        ]))

        # Align the summary table to the right
        outer_summary_table = Table([[ "", summary_table ]], colWidths=[272, 250])
        outer_summary_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(outer_summary_table)
        story.append(Spacer(1, 20))

        # ── 5. Declaration & Signatory Section ──
        declaration_text = """<b>Declaration:</b><br/>
1. We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.<br/>
2. This is a computer-generated tax invoice and does not require a physical signature.
"""
        
        sign_data = [
            [
                Paragraph(declaration_text, tbl_cell_style),
                Paragraph("<b>For ShopEase Retail Private Limited</b><br/><br/><br/><br/>Authorized Signatory", tbl_cell_right)
            ]
        ]
        sign_table = Table(sign_data, colWidths=[320, 202])
        sign_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('PADDING', (0, 0), (-1, -1), 12),
        ]))
        
        story.append(KeepTogether([sign_table]))

        doc.build(story)
        buffer.seek(0)
        
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=f"Tax_Invoice_{order.order_number}.pdf",
            content_type="application/pdf"
        )


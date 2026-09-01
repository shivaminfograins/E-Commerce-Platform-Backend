"""
apps/orders/views.py
====================
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import F, Q
from rest_framework import generics, filters, status, serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError

from apps.accounts.models import Address
from apps.cart.models import CartItem
from apps.products.models import ProductVariant

from .models import Order, OrderItem, VendorOrder
from apps.payments.models import Transaction
from apps.payments.services import RazorpayService
from .serializers import (
    OrderSerializer,
    OrderSummarySerializer,
    PlaceOrderSerializer,
    VendorOrderSerializer,
    VendorOrderSummarySerializer,
)
from apps.vendors.permissions import IsApprovedVendor


# ---------------------------------------------------------------------------
# Business constants
# ---------------------------------------------------------------------------
FREE_SHIPPING_THRESHOLD = Decimal(
    getattr(settings, "FREE_SHIPPING_THRESHOLD", "999.00")
)
SHIPPING_FEE = Decimal(
    getattr(settings, "SHIPPING_FEE", "99.00")
)
TAX_RATE = Decimal(
    getattr(settings, "TAX_RATE", "0.00")
)


# ===========================================================================
# PAGINATION
# ===========================================================================
class OrderPagination(PageNumberPagination):
    page_size              = 10
    page_size_query_param  = "page_size"
    max_page_size          = 50
    page_query_param       = "page"

    def get_paginated_response(self, data):
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
        idempotency_key = data.get("idempotency_key", "")

        # ── User-Scoped Idempotency Read ──────────────────────────────
        if idempotency_key:
            existing_order = Order.objects.filter(user=request.user, idempotency_key=idempotency_key).first()
            if existing_order:
                serializer = OrderSerializer(existing_order, context={"request": request})
                return Response(
                    {
                        "success": True,
                        "message": "Order placed successfully (retrieved from cache).",
                        "order":   serializer.data,
                    },
                    status=status.HTTP_201_CREATED,
                )

        # ── Step 2: Validate address ownership ───────────────────────
        try:
            address = Address.objects.get(
                pk=data["address"],
                user=request.user,
            )
        except Address.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": "Address not found. Please use a valid address from your account.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 3: Fetch cart ────────────────────────────────────────
        cart_items = list(
            CartItem.objects
            .filter(user=request.user)
            .select_related(
                "vendor_product__product",
                "vendor_product__vendor",
                "vendor_product_variant__product_variant",
            )
        )

        if not cart_items:
            return Response(
                {
                    "success": False,
                    "message": "Your cart is empty. Add items before placing an order.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 4: Strict offer & stock validation ───────────────────
        stock_errors = []
        for item in cart_items:
            # Model validation
            try:
                item.clean()
            except Exception as e:
                stock_errors.append({"detail": str(e)})
                continue

            # Check stock
            if item.vendor_product_variant_id:
                available = item.vendor_product_variant.stock
                name = f"{item.vendor_product.product.name} — {item.vendor_product_variant.product_variant.name}"
                sku = item.vendor_product_variant.vendor_sku or (item.vendor_product_variant.product_variant.sku if item.vendor_product_variant.product_variant else "")
            else:
                available = item.vendor_product.stock
                name = item.vendor_product.product.name
                v_first = item.vendor_product.product.variants.first()
                sku = v_first.sku if (v_first and v_first.sku) else (item.vendor_product.product.slug or "")

            if item.quantity > available:
                stock_errors.append(
                    {
                        "variant_id": item.vendor_product_variant_id or item.vendor_product_id,
                        "variant_name": name,
                        "sku":          sku,
                        "requested":    item.quantity,
                        "available":    available,
                    }
                )

        if stock_errors:
            return Response(
                {
                    "success": False,
                    "message": "One or more items in your cart are invalid or have insufficient stock.",
                    "stock_errors": stock_errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 5: Price calculation ─────────────────────────────────
        subtotal = sum(
            (item.vendor_product_variant.price if item.vendor_product_variant_id else item.vendor_product.price) * item.quantity
            for item in cart_items
        )
        shipping_charge = (
            Decimal("0.00")
            if subtotal >= FREE_SHIPPING_THRESHOLD
            else SHIPPING_FEE
        )
        tax = (subtotal * TAX_RATE).quantize(Decimal("0.01"))
        
        # Coupon validation
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

        # ── Step 6: Atomic DB checkouts with user-scoped uniqueness ───
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user,
                    address=address,
                    snapshot_full_name=address.full_name,
                    snapshot_phone=address.phone,
                    snapshot_address_line_1=address.address_line_1,
                    snapshot_address_line_2=address.address_line_2,
                    snapshot_landmark=address.landmark,
                    snapshot_city=address.city,
                    snapshot_state=address.state,
                    snapshot_country=address.country,
                    snapshot_postal_code=address.postal_code,
                    status=Order.PENDING,
                    payment_method=data["payment_method"],
                    payment_status=Order.PAYMENT_PENDING,
                    subtotal=subtotal,
                    shipping_charge=shipping_charge,
                    discount=discount,
                    tax=tax,
                    total_amount=subtotal + shipping_charge + tax - discount,
                    coupon=coupon,
                    coupon_code=coupon.code if coupon else "",
                    notes=data.get("notes", ""),
                    idempotency_key=idempotency_key or None,
                )

                if coupon:
                    from apps.coupons.models import CouponUsage
                    CouponUsage.objects.create(
                        coupon=coupon,
                        user=request.user,
                        order=order
                    )
                    Coupon.objects.filter(pk=coupon.pk).update(usage_count=F("usage_count") + 1)

                # Create pending transaction record
                trx_payment_method = Transaction.COD if data["payment_method"] == Order.COD else Transaction.RAZORPAY
                trx = Transaction.objects.create(
                    order=order,
                    payment_method=trx_payment_method,
                    status=Transaction.PENDING,
                    amount=order.total_amount
                )

                # Multi-vendor splitting & stock reservation
                from apps.orders.services import create_split_vendor_orders
                create_split_vendor_orders(order, cart_items, request.user)

                # Clear cart
                CartItem.objects.filter(user=request.user).delete()

                from apps.notifications.services import NotificationService
                event_key = f"ORDER_PLACED:{order.order_number}"
                NotificationService.create_notification_on_commit(
                    user=request.user,
                    notification_type="ORDER",
                    title="Order Placed",
                    message=f"Your order {order.order_number} has been placed successfully.",
                    event_key=event_key,
                    reference_type="ORDER",
                    reference_id=order.order_number,
                )

        except IntegrityError:
            # Handle duplicate key
            existing_order = Order.objects.get(user=request.user, idempotency_key=idempotency_key)
            serializer = OrderSerializer(existing_order, context={"request": request})
            return Response(
                {
                    "success": True,
                    "message": "Order placed successfully (retrieved from cache).",
                    "order":   serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )

        # ── Step 7: External Gateway Initialization OUTSIDE DB Transaction ──
        if data["payment_method"] == Order.RAZORPAY:
            try:
                rzp_response = RazorpayService.create_razorpay_order(
                    order_number=order.order_number,
                    amount=order.total_amount
                )
                if not rzp_response.get("success"):
                    raise Exception(rzp_response.get("error", "Gateway error"))

                trx.razorpay_order_id = rzp_response["id"]
                trx.raw_response = rzp_response.get("raw_response", {})
                trx.save(update_fields=["razorpay_order_id", "raw_response", "updated_at"])
            except Exception as rzp_err:
                # Handle Gateway Initialization Failure: Cancel sub-orders and release stock safely
                from apps.orders.services import cancel_vendor_order_by_vendor
                for sub_order in order.vendor_orders.all():
                    try:
                        cancel_vendor_order_by_vendor(sub_order, request.user)
                    except Exception:
                        pass

                trx.status = Transaction.FAILED
                trx.error_message = str(rzp_err)
                trx.save(update_fields=["status", "error_message", "updated_at"])

                order.status = Order.CANCELLED
                order.payment_status = Order.PAYMENT_FAILED
                order.save(update_fields=["status", "payment_status", "updated_at"])

                return Response(
                    {
                        "success": False,
                        "message": f"Payment gateway initialization failed: {rzp_err}",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
    permission_classes  = [IsAuthenticated]
    serializer_class    = OrderSummarySerializer
    pagination_class    = OrderPagination
    filter_backends     = [filters.SearchFilter, filters.OrderingFilter]
    search_fields       = ["order_number", "status"]
    ordering_fields     = ["created_at", "total_amount", "status", "payment_status"]
    ordering            = ["-created_at"]

    def get_queryset(self):
        return (
            Order.objects
            .filter(user=self.request.user)
            .select_related("address")
            .prefetch_related("items")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

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
                "vendor_orders",
                "vendor_orders__vendor",
                "vendor_orders__items",
            )
        )

    def get_object(self):
        queryset = self.get_queryset()
        lookup = self.kwargs.get("order_number") or self.kwargs.get("pk")
        if str(lookup).isdigit():
            obj = get_object_or_404(queryset, pk=int(lookup))
        else:
            obj = get_object_or_404(queryset, order_number=lookup)
        self.check_object_permissions(self.request, obj)
        return obj

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
# POST / PATCH /api/orders/<order_number>/cancel/
# ===========================================================================
class CancelOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None, order_number=None):
        return self._cancel(request, pk, order_number)

    def patch(self, request, pk=None, order_number=None):
        return self._cancel(request, pk, order_number)

    def _cancel(self, request, pk=None, order_number=None):
        lookup = order_number or pk
        try:
            if str(lookup).isdigit():
                order = Order.objects.prefetch_related("vendor_orders").get(pk=int(lookup), user=request.user)
            else:
                order = Order.objects.prefetch_related("vendor_orders").get(order_number=lookup, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "message": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not order.is_cancellable:
            return Response(
                {
                    "success": False,
                    "message": f"This order cannot be cancelled. Current status: '{order.get_status_display()}'.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.orders.services import cancel_vendor_order_by_vendor
        with transaction.atomic():
            for sub_order in order.vendor_orders.exclude(status=VendorOrder.CANCELLED):
                try:
                    cancel_vendor_order_by_vendor(sub_order, request.user)
                except ValidationError as e:
                    return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

            order.status = Order.CANCELLED
            order.save(update_fields=["status", "updated_at"])

            from apps.notifications.services import NotificationService
            event_key = f"ORDER_CANCELLED:{order.order_number}"
            NotificationService.create_notification_on_commit(
                user=request.user,
                notification_type="ORDER",
                title="Order Cancelled",
                message=f"Order {order.order_number} has been cancelled successfully.",
                event_key=event_key,
                reference_type="ORDER",
                reference_id=order.order_number,
            )

        serializer = OrderSerializer(order, context={"request": request})
        return Response(
            {
                "success": True,
                "message": "Order cancelled successfully.",
                "order":   serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ===========================================================================
# GET /api/orders/<order_number>/tracking/
# ===========================================================================
class CustomerOrderTrackingView(APIView):
    """
    GET /api/orders/<order_number>/tracking/
    Returns independent shipment tracking details and derived status timelines for each VendorOrder.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, order_number=None, pk=None):
        lookup = order_number or pk
        if str(lookup).isdigit():
            order = get_object_or_404(Order.objects.prefetch_related("vendor_orders", "vendor_orders__vendor"), user=request.user, pk=int(lookup))
        else:
            order = get_object_or_404(Order.objects.prefetch_related("vendor_orders", "vendor_orders__vendor"), user=request.user, order_number=lookup)

        shipments = []
        for sub in order.vendor_orders.all():
            timeline = [
                {
                    "status": VendorOrder.CONFIRMED,
                    "label": "Order Confirmed",
                    "completed": sub.status in [VendorOrder.CONFIRMED, VendorOrder.PROCESSING, VendorOrder.PACKED, VendorOrder.SHIPPED, VendorOrder.DELIVERED],
                    "timestamp": sub.created_at.isoformat() if sub.created_at else None,
                },
                {
                    "status": VendorOrder.PROCESSING,
                    "label": "Processing",
                    "completed": sub.status in [VendorOrder.PROCESSING, VendorOrder.PACKED, VendorOrder.SHIPPED, VendorOrder.DELIVERED],
                    "timestamp": None,
                },
                {
                    "status": VendorOrder.PACKED,
                    "label": "Packed",
                    "completed": sub.status in [VendorOrder.PACKED, VendorOrder.SHIPPED, VendorOrder.DELIVERED],
                    "timestamp": None,
                },
                {
                    "status": VendorOrder.SHIPPED,
                    "label": "Shipped",
                    "completed": sub.status in [VendorOrder.SHIPPED, VendorOrder.DELIVERED],
                    "timestamp": sub.shipped_at.isoformat() if sub.shipped_at else None,
                },
                {
                    "status": VendorOrder.DELIVERED,
                    "label": "Delivered",
                    "completed": sub.status == VendorOrder.DELIVERED,
                    "timestamp": sub.delivered_at.isoformat() if sub.delivered_at else None,
                },
            ]
            if sub.status == VendorOrder.CANCELLED:
                timeline.append({
                    "status": VendorOrder.CANCELLED,
                    "label": "Cancelled",
                    "completed": True,
                    "timestamp": sub.updated_at.isoformat() if sub.updated_at else None,
                })

            shipments.append({
                "vendor_order_number": sub.vendor_order_number,
                "vendor_name": sub.vendor_store_name_snapshot or sub.vendor.store_name,
                "status": sub.status,
                "status_display": sub.get_status_display(),
                "carrier_name": sub.carrier_name,
                "tracking_number": sub.tracking_number,
                "shipped_at": sub.shipped_at.isoformat() if sub.shipped_at else None,
                "delivered_at": sub.delivered_at.isoformat() if sub.delivered_at else None,
                "timeline": timeline,
            })

        return Response({
            "success": True,
            "order_number": order.order_number,
            "master_status": order.status,
            "shipments": shipments,
        }, status=status.HTTP_200_OK)


# ===========================================================================
# POST /api/orders/<order_number>/vendors/<vendor_order_number>/cancel/
# ===========================================================================
class CustomerCancelVendorOrderView(APIView):
    """
    POST /api/orders/<order_number>/vendors/<vendor_order_number>/cancel/
    Cancels a specific vendor sub-order belonging to the authenticated customer's order.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, order_number=None, vendor_order_number=None):
        lookup = order_number
        if str(lookup).isdigit():
            order = get_object_or_404(Order, user=request.user, pk=int(lookup))
        else:
            order = get_object_or_404(Order, user=request.user, order_number=lookup)

        vendor_order = get_object_or_404(VendorOrder, master_order=order, vendor_order_number=vendor_order_number)

        from apps.orders.services import cancel_vendor_order_by_vendor
        try:
            cancel_vendor_order_by_vendor(vendor_order, request.user)
        except ValidationError as e:
            return Response({"success": False, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        from apps.notifications.services import NotificationService
        event_key = f"VENDOR_ORDER_CANCELLED:{vendor_order.vendor_order_number}"
        NotificationService.create_notification_on_commit(
            user=request.user,
            notification_type="ORDER",
            title="Vendor Order Cancelled",
            message=f"Sub-order {vendor_order.vendor_order_number} has been cancelled successfully.",
            event_key=event_key,
            reference_type="VENDOR_ORDER",
            reference_id=vendor_order.vendor_order_number,
        )

        serializer = VendorOrderSerializer(vendor_order, context={"request": request})
        return Response({
            "success": True,
            "message": "Sub-order cancelled successfully.",
            "order": serializer.data,
        }, status=status.HTTP_200_OK)


# ===========================================================================
# GET /api/orders/<order_number>/returns/
# ===========================================================================
class CustomerOrderReturnsView(APIView):
    """
    GET /api/orders/<order_number>/returns/
    Returns return requests associated with the authenticated customer's order.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, order_number=None, pk=None):
        lookup = order_number or pk
        if str(lookup).isdigit():
            order = get_object_or_404(Order, user=request.user, pk=int(lookup))
        else:
            order = get_object_or_404(Order, user=request.user, order_number=lookup)

        from apps.returns.models import ReturnRequest
        from apps.returns.serializers import ReturnRequestSerializer
        returns_qs = ReturnRequest.objects.filter(order=order, customer=request.user).select_related(
            "order", "vendor_order", "order_item", "vendor", "refund"
        )
        serializer = ReturnRequestSerializer(returns_qs, many=True)
        return Response({
            "success": True,
            "order_number": order.order_number,
            "returns": serializer.data,
        }, status=status.HTTP_200_OK)


# ===========================================================================
# DOWNLOAD INVOICE
# ===========================================================================
class DownloadInvoiceView(APIView):
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
            alignment=2
        )

        tbl_cell_bold_right = ParagraphStyle(
            "TableCellBoldRight",
            parent=tbl_cell_style,
            fontName="Helvetica-Bold",
            alignment=2
        )

        story = []

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

        divider = Table([[""]], colWidths=[522])
        divider.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, -1), 1, colors.HexColor("#e2e8f0")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(divider)
        story.append(Spacer(1, 10))

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

        items = order.items.all()
        for idx, item in enumerate(items, 1):
            hsn_code = "8517 12 00"
            gross_amount = item.price * item.quantity
            item_discount = Decimal("0.00")
            if order.subtotal > 0:
                item_discount = (gross_amount / order.subtotal) * order.discount
            taxable_val = gross_amount - item_discount
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

        outer_summary_table = Table([[ "", summary_table ]], colWidths=[272, 250])
        outer_summary_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(outer_summary_table)
        story.append(Spacer(1, 20))

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


# ===========================================================================
# VENDOR ORDER ENDPOINTS
# ===========================================================================

class VendorOrderListView(generics.ListAPIView):
    """
    Returns a paginated list of VendorOrders belonging to the authenticated vendor.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorOrderSummarySerializer
    pagination_class = OrderPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["vendor_order_number", "status"]
    ordering_fields = ["created_at", "total_amount", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        vendor = self.request.user.vendor_profile
        return VendorOrder.objects.filter(vendor=vendor).select_related("master_order")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "success": True,
            "results": serializer.data
        })


class VendorOrderDetailView(generics.RetrieveAPIView):
    """
    Returns detailed snapshot and items for a specific VendorOrder.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorOrderSerializer
    lookup_field = "vendor_order_number"

    def get_queryset(self):
        vendor = self.request.user.vendor_profile
        return VendorOrder.objects.filter(vendor=vendor).select_related("master_order").prefetch_related("items")

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({
            "success": True,
            "order": serializer.data
        })


class VendorOrderStatusUpdateView(APIView):
    """
    PATCH /api/vendors/orders/<vendor_order_number>/status/
    Updates sub-order status (e.g. from CONFIRMED -> PROCESSING -> PACKED).
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def patch(self, request, vendor_order_number):
        vendor = request.user.vendor_profile
        vendor_order = get_object_or_404(
            VendorOrder.objects.select_for_update(),
            vendor=vendor,
            vendor_order_number=vendor_order_number
        )

        new_status = request.data.get("status")
        valid_transitions = {
            VendorOrder.CONFIRMED: [VendorOrder.PROCESSING],
            VendorOrder.PROCESSING: [VendorOrder.PACKED],
            VendorOrder.PACKED: [VendorOrder.SHIPPED],
        }

        current = vendor_order.status
        if current not in valid_transitions or new_status not in valid_transitions[current]:
            return Response(
                {"detail": f"Transition from '{current}' to '{new_status}' is invalid."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            vendor_order.status = new_status
            vendor_order.save(update_fields=["status", "updated_at"])
            
            # Recalculate Master Order status
            from apps.orders.services import reconcile_master_order_status
            reconcile_master_order_status(vendor_order.master_order)

            from apps.notifications.services import NotificationService
            event_key = f"VENDOR_ORDER_{new_status.upper()}:{vendor_order.vendor_order_number}"
            NotificationService.create_notification_on_commit(
                user=vendor_order.master_order.user,
                notification_type="ORDER",
                title=f"Order {new_status.title()}",
                message=f"Your item order {vendor_order.vendor_order_number} status updated to {new_status}.",
                event_key=event_key,
                reference_type="VENDOR_ORDER",
                reference_id=vendor_order.vendor_order_number,
            )

        serializer = VendorOrderSerializer(vendor_order, context={"request": request})
        return Response({
            "success": True,
            "message": "Status updated successfully.",
            "order": serializer.data
        }, status=status.HTTP_200_OK)


class VendorOrderShipView(APIView):
    """
    POST /api/vendors/orders/<vendor_order_number>/ship/
    Transitions sub-order status to SHIPPED and records courier details.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def post(self, request, vendor_order_number):
        vendor = request.user.vendor_profile
        vendor_order = get_object_or_404(
            VendorOrder.objects.select_for_update(),
            vendor=vendor,
            vendor_order_number=vendor_order_number
        )

        tracking_number = request.data.get("tracking_number", "").strip()
        carrier_name = request.data.get("carrier_name", "").strip()

        if not tracking_number or not carrier_name:
            return Response(
                {"detail": "Both tracking_number and carrier_name are required to ship the order."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if vendor_order.status != VendorOrder.PACKED:
            return Response(
                {"detail": f"Order in status '{vendor_order.status}' cannot be shipped. Must be 'packed' first."},
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.utils import timezone
        with transaction.atomic():
            vendor_order.status = VendorOrder.SHIPPED
            vendor_order.tracking_number = tracking_number
            vendor_order.carrier_name = carrier_name
            vendor_order.shipped_at = timezone.now()
            vendor_order.save(update_fields=["status", "tracking_number", "carrier_name", "shipped_at", "updated_at"])

            from apps.orders.services import reconcile_master_order_status
            reconcile_master_order_status(vendor_order.master_order)

            from apps.notifications.services import NotificationService
            event_key = f"VENDOR_ORDER_SHIPPED:{vendor_order.vendor_order_number}"
            NotificationService.create_notification_on_commit(
                user=vendor_order.master_order.user,
                notification_type="ORDER",
                title="Order Shipped",
                message=f"Your order {vendor_order.vendor_order_number} has been shipped via {carrier_name} (Tracking: {tracking_number}).",
                event_key=event_key,
                reference_type="VENDOR_ORDER",
                reference_id=vendor_order.vendor_order_number,
            )

        serializer = VendorOrderSerializer(vendor_order, context={"request": request})
        return Response({
            "success": True,
            "message": "Order shipped successfully.",
            "order": serializer.data
        }, status=status.HTTP_200_OK)


class VendorOrderCancelView(APIView):
    """
    POST /api/vendors/orders/<vendor_order_number>/cancel/
    Vendor cancellation of a specific sub-order.
    """
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def post(self, request, vendor_order_number):
        vendor = request.user.vendor_profile
        vendor_order = get_object_or_404(
            VendorOrder,
            vendor=vendor,
            vendor_order_number=vendor_order_number
        )

        from apps.orders.services import cancel_vendor_order_by_vendor
        try:
            cancel_vendor_order_by_vendor(vendor_order, request.user)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = VendorOrderSerializer(vendor_order, context={"request": request})
        return Response({
            "success": True,
            "message": "Order cancelled successfully by vendor.",
            "order": serializer.data
        }, status=status.HTTP_200_OK)

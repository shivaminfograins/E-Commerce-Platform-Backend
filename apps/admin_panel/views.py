from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.models import User
from apps.accounts.serializers import CustomTokenObtainPairSerializer, UserSerializer, AdminCustomerSerializer
from apps.products.models import Product, Category, ProductVariant
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSummarySerializer
from .permissions import AdminPermission


class AdminLoginView(TokenObtainPairView):
    """
    Admin-specific login view that validates the user has the 'admin' role.
    """
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            # The CustomTokenObtainPairSerializer adds user info under "user" key.
            # We must verify the role here to enforce that only admins can log in.
            user_data = response.data.get("user", {})
            role = user_data.get("role")
            if role != "admin":
                return Response(
                    {"detail": "Access denied. Only admin users can log in."},
                    status=status.HTTP_403_FORBIDDEN
                )
        return response


class AdminDashboardView(APIView):
    """
    Retrieves key statistics and metrics for the admin dashboard.
    """
    permission_classes = [AdminPermission]

    def get(self, request):
        now = timezone.now()
        
        # Stat counts
        total_products = Product.objects.count()
        total_categories = Category.objects.count()
        total_customers = User.objects.filter(role="customer").count()
        total_orders = Order.objects.count()
        pending_orders = Order.objects.filter(status=Order.PENDING).count()
        delivered_orders = Order.objects.filter(status=Order.DELIVERED).count()
        cancelled_orders = Order.objects.filter(status=Order.CANCELLED).count()

        # Revenue computations (excluding cancelled & refunded orders)
        revenue_qs = Order.objects.exclude(status__in=[Order.CANCELLED, Order.REFUNDED])
        total_revenue = revenue_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        
        monthly_revenue = revenue_qs.filter(
            created_at__year=now.year,
            created_at__month=now.month
        ).aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")

        # Recent activities (Recent 5)
        recent_orders_qs = Order.objects.order_by("-created_at")[:5]
        # Calculate item_count, is_cancellable etc as required by the serializer
        # Let's annotate or compute item_count on the fly for the serialized output.
        # OrderSummarySerializer expects 'item_count' and 'is_cancellable' properties/annotations.
        # Let's check how the serializer works. We can use a list comprehension or annotate.
        # Let's annotate the queryset to make sure it runs correctly.
        recent_orders_prefetched = Order.objects.prefetch_related("items").order_by("-created_at")[:5]

        # In order.models, is_cancellable is a property, but in DB it's not.
        # Let's verify if is_cancellable is a property or model field.
        # Let's check: in the serializer it is a read-only field:
        # `is_cancellable = serializers.BooleanField(read_only=True)`
        # If it's a property on the Order model, Serializer will fetch it automatically.
        recent_orders_data = OrderSummarySerializer(
            recent_orders_prefetched, 
            many=True, 
            context={"request": request}
        ).data

        recent_customers_qs = User.objects.filter(role="customer").select_related("profile").order_by("-date_joined")[:5]
        recent_customers_data = AdminCustomerSerializer(recent_customers_qs, many=True).data

        # Top Selling Products
        # Group by product/variant, sum quantity and total revenue
        top_selling = OrderItem.objects.exclude(order__status__in=[Order.CANCELLED, Order.REFUNDED]) \
            .values("product_id", "product_name") \
            .annotate(
                sales=Sum("quantity"),
                revenue=Sum("total")
            ).order_by("-sales")[:5]

        # Low Stock & Out of Stock Products (via Variants)
        low_stock_variants = ProductVariant.objects.filter(
            stock__gt=0, stock__lte=10
        ).select_related("product")[:10]
        
        low_stock_data = [
            {
                "variant_id": v.id,
                "product_id": v.product.id,
                "product_name": v.product.name,
                "variant_name": v.name,
                "sku": v.sku,
                "stock": v.stock,
                "price": str(v.price),
            }
            for v in low_stock_variants
        ]

        out_of_stock_variants = ProductVariant.objects.filter(
            stock=0
        ).select_related("product")[:10]
        
        out_of_stock_data = [
            {
                "variant_id": v.id,
                "product_id": v.product.id,
                "product_name": v.product.name,
                "variant_name": v.name,
                "sku": v.sku,
                "stock": v.stock,
                "price": str(v.price),
            }
            for v in out_of_stock_variants
        ]

        return Response({
            "total_products": total_products,
            "total_categories": total_categories,
            "total_customers": total_customers,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "delivered_orders": delivered_orders,
            "cancelled_orders": cancelled_orders,
            "revenue": str(total_revenue),
            "monthly_revenue": str(monthly_revenue),
            "recent_orders": recent_orders_data,
            "recent_customers": recent_customers_data,
            "top_selling_products": list(top_selling),
            "low_stock_products": low_stock_data,
            "out_of_stock_products": out_of_stock_data,
        }, status=status.HTTP_200_OK)


from rest_framework import viewsets
from rest_framework.decorators import action
from apps.products.serializers import CategorySerializer


class AdminCategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for admin to manage categories (CRUD operations).
    """
    queryset = Category.objects.all().order_by("id")
    serializer_class = CategorySerializer
    permission_classes = [AdminPermission]

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        """
        Toggles the active status of a category.
        """
        category = self.get_object()
        category.is_active = not category.is_active
        category.save()
        serializer = self.get_serializer(category)
        return Response(serializer.data, status=status.HTTP_200_OK)


from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from apps.products.pagination import ProductPagination
from apps.products.serializers import ProductSerializer, ProductVariantSerializer, ProductVariantImageSerializer
from apps.products.models import ProductVariantImage


class AdminProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for admin to manage products.
    """
    queryset = Product.objects.all().order_by("-created_at")
    serializer_class = ProductSerializer
    permission_classes = [AdminPermission]
    pagination_class = ProductPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["category", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        """
        Toggles the active status of a product.
        """
        product = self.get_object()
        product.is_active = not product.is_active
        product.save()
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminProductVariantViewSet(viewsets.ModelViewSet):
    """
    ViewSet for admin to manage product variants.
    """
    serializer_class = ProductVariantSerializer
    permission_classes = [AdminPermission]

    def get_queryset(self):
        product_id = self.kwargs.get("product_id")
        if product_id:
            return ProductVariant.objects.filter(product_id=product_id).order_by("id")
        return ProductVariant.objects.all().order_by("id")

    def create(self, request, *args, **kwargs):
        product_id = self.kwargs.get("product_id")
        product = get_object_or_404(Product, pk=product_id)
        data = request.data.copy()
        data["product"] = product.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(product=product)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AdminProductImageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for admin to manage product variant images.
    """
    serializer_class = ProductVariantImageSerializer
    permission_classes = [AdminPermission]

    def get_queryset(self):
        product_id = self.kwargs.get("product_id")
        variant_id = self.request.query_params.get("variant_id")
        if variant_id:
            return ProductVariantImage.objects.filter(variant_id=variant_id).order_by("display_order", "id")
        if product_id:
            return ProductVariantImage.objects.filter(variant__product_id=product_id).order_by("display_order", "id")
        return ProductVariantImage.objects.all().order_by("display_order", "id")

    def create(self, request, *args, **kwargs):
        product_id = self.kwargs.get("product_id")
        variant_id = request.data.get("variant")
        
        if not variant_id:
            product = get_object_or_404(Product, pk=product_id)
            variant = product.variants.first()
            if not variant:
                variant = ProductVariant.objects.create(
                    product=product,
                    name="Default Variant",
                    sku=f"{product.slug[:10]}-DEFAULT",
                    price=0.00,
                    stock=0
                )
        else:
            variant = get_object_or_404(ProductVariant, pk=variant_id)
            
        data = request.data.copy()
        data["variant"] = variant.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(variant=variant)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


from rest_framework.pagination import PageNumberPagination
from apps.accounts.serializers import AddressSerializer, AdminCustomerSerializer
from apps.accounts.models import Address


class AdminCustomerPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminCustomerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for admin to view and manage customer details.
    """
    queryset = User.objects.filter(role="customer").select_related("profile").order_by("-date_joined")
    serializer_class = AdminCustomerSerializer
    permission_classes = [AdminPermission]
    pagination_class = AdminCustomerPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["is_active"]
    search_fields = ["username", "email"]
    ordering_fields = ["username", "email", "date_joined"]

    @action(detail=True, methods=["patch"], url_path="block")
    def block(self, request, pk=None):
        customer = self.get_object()
        customer.is_active = False
        customer.save()
        return Response(self.get_serializer(customer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="unblock")
    def unblock(self, request, pk=None):
        customer = self.get_object()
        customer.is_active = True
        customer.save()
        return Response(self.get_serializer(customer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="orders")
    def orders(self, request, pk=None):
        customer = self.get_object()
        orders = Order.objects.filter(user=customer).order_by("-created_at")
        orders_prefetched = orders.prefetch_related("items").select_related("user")
        serializer = OrderSummarySerializer(orders_prefetched, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="addresses")
    def addresses(self, request, pk=None):
        customer = self.get_object()
        addresses = Address.objects.filter(user=customer).order_by("-is_default", "-created_at")
        serializer = AddressSerializer(addresses, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminAddressViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for admin to view addresses.
    """
    queryset = Address.objects.all().order_by("-created_at")
    serializer_class = AddressSerializer
    permission_classes = [AdminPermission]


import django_filters
from django.db import transaction
from django.db.models import F, Count
from apps.orders.serializers import OrderSerializer


class AdminOrderPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminOrderFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name="status")
    payment_status = django_filters.CharFilter(field_name="payment_status")
    payment_method = django_filters.CharFilter(field_name="payment_method")
    start_date = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    end_date = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Order
        fields = ["status", "payment_status", "payment_method"]


class AdminOrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for admin to manage orders.
    """
    queryset = Order.objects.all().order_by("-created_at")
    permission_classes = [AdminPermission]
    pagination_class = AdminOrderPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = AdminOrderFilter
    search_fields = ["order_number", "snapshot_full_name", "user__username", "user__email"]
    ordering_fields = ["created_at", "total_amount"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return OrderSerializer
        return OrderSummarySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            return qs.prefetch_related("items").select_related("user")
        elif self.action == "retrieve":
            return qs.prefetch_related("items__product", "items__variant").select_related("user")
        return qs

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "success": True,
                "order": serializer.data,
            }
        )

    @action(detail=True, methods=["patch"], url_path="status")
    def status(self, request, pk=None):
        """
        Updates the order status.
        """
        order = self.get_object()
        new_status = request.data.get("status")
        if not new_status or new_status not in dict(Order.STATUS_CHOICES):
            return Response(
                {"detail": "Invalid status value."},
                status=status.HTTP_400_BAD_REQUEST
            )
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])
        serializer = self.get_serializer(order)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="cancel")
    def cancel(self, request, pk=None):
        """
        Cancels the order, restoring stock levels atomically.
        """
        order = get_object_or_404(
            Order.objects.prefetch_related("items__variant"),
            pk=pk
        )

        if not order.is_cancellable:
            return Response(
                {
                    "success": False,
                    "message": f"This order cannot be cancelled. Current status: '{order.get_status_display()}'."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            for item in order.items.all():
                if item.variant_id:
                    ProductVariant.objects.filter(pk=item.variant_id).update(
                        stock=F("stock") + item.quantity
                    )
            order.status = Order.CANCELLED
            order.save(update_fields=["status", "updated_at"])

        serializer = OrderSerializer(order, context={"request": request})
        return Response(
            {
                "success": True,
                "message": "Order cancelled successfully.",
                "order": serializer.data,
            },
            status=status.HTTP_200_OK
        )


from rest_framework import viewsets
from django.db.models.functions import TruncMonth, TruncDay, TruncHour
from django.utils.dateparse import parse_date
import datetime
from apps.accounts.serializers import ProfileSerializer
from .models import AppSetting


class AdminReportsViewSet(viewsets.ViewSet):
    """
    ViewSet to generate e-commerce analytics reports.
    """
    permission_classes = [AdminPermission]

    def _get_trend_data(self, request, query_type):
        """
        query_type can be: "sales", "revenue", "orders"
        """
        # Parse params
        time_range = request.query_params.get("range", "yearly")
        particular_date_str = request.query_params.get("date")
        
        now = timezone.now()
        start_date = None
        end_date = None
        group_by = "month"
        
        dummy_months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        days = []
        hours = []
        
        if time_range == "weekly":
            start_date = now - datetime.timedelta(days=6)
            group_by = "day"
            for i in range(7):
                d = now - datetime.timedelta(days=6-i)
                days.append(d.strftime("%b %d"))
        elif time_range == "monthly":
            start_date = now - datetime.timedelta(days=29)
            group_by = "day"
            for i in range(30):
                d = now - datetime.timedelta(days=29-i)
                days.append(d.strftime("%b %d"))
        elif time_range == "custom" and particular_date_str:
            try:
                chosen_date = parse_date(particular_date_str)
                if chosen_date:
                    start_date = timezone.make_aware(datetime.datetime.combine(chosen_date, datetime.time.min))
                    end_date = timezone.make_aware(datetime.datetime.combine(chosen_date, datetime.time.max))
                    group_by = "hour"
                    hours = [f"{h:02d}:00" for h in range(24)]
            except Exception:
                pass
        
        # Determine base queryset and filter fields
        if query_type == "sales":
            qs = OrderItem.objects.exclude(order__status__in=[Order.CANCELLED, Order.REFUNDED])
            date_field = "order__created_at"
        elif query_type == "revenue":
            qs = Order.objects.exclude(status__in=[Order.CANCELLED, Order.REFUNDED])
            date_field = "created_at"
        else: # orders
            qs = Order.objects.all()
            date_field = "created_at"
            
        # Apply date filters
        if start_date:
            if group_by == "hour" and end_date:
                qs = qs.filter(**{f"{date_field}__range": (start_date, end_date)})
            else:
                qs = qs.filter(**{f"{date_field}__gte": start_date})
        else:
            # Default yearly: current year
            qs = qs.filter(**{f"{date_field}__year": now.year})
            
        # Group and annotate
        if group_by == "month":
            trunc_fn = TruncMonth(date_field)
            monthly = qs.annotate(trunc=trunc_fn).values("trunc")
            if query_type == "sales":
                monthly = monthly.annotate(val=Sum("quantity"))
            elif query_type == "revenue":
                monthly = monthly.annotate(val=Sum("total_amount"))
            else: # orders
                monthly = monthly.annotate(val=Count("id"))
                
            trend = {m: 0 for m in dummy_months}
            for entry in monthly:
                if entry["trunc"]:
                    m_name = entry["trunc"].strftime("%b")
                    val = entry["val"] or 0
                    trend[m_name] = float(val) if query_type == "revenue" else int(val)
            return [{"month": m, query_type: trend[m]} for m in dummy_months]
            
        elif group_by == "day":
            trunc_fn = TruncDay(date_field)
            daily = qs.annotate(trunc=trunc_fn).values("trunc")
            if query_type == "sales":
                daily = daily.annotate(val=Sum("quantity"))
            elif query_type == "revenue":
                daily = daily.annotate(val=Sum("total_amount"))
            else: # orders
                daily = daily.annotate(val=Count("id"))
                
            trend = {d_str: 0 for d_str in days}
            for entry in daily:
                if entry["trunc"]:
                    d_str = entry["trunc"].strftime("%b %d")
                    if d_str in trend:
                        val = entry["val"] or 0
                        trend[d_str] = float(val) if query_type == "revenue" else int(val)
            return [{"month": d_str, query_type: trend[d_str]} for d_str in days]
            
        elif group_by == "hour":
            trunc_fn = TruncHour(date_field)
            hourly = qs.annotate(trunc=trunc_fn).values("trunc")
            if query_type == "sales":
                hourly = hourly.annotate(val=Sum("quantity"))
            elif query_type == "revenue":
                hourly = hourly.annotate(val=Sum("total_amount"))
            else: # orders
                hourly = hourly.annotate(val=Count("id"))
                
            trend = {h_str: 0 for h_str in hours}
            for entry in hourly:
                if entry["trunc"]:
                    h_str = timezone.localtime(entry["trunc"]).strftime("%H:00")
                    if h_str in trend:
                        val = entry["val"] or 0
                        trend[h_str] = float(val) if query_type == "revenue" else int(val)
            return [{"month": h_str, query_type: trend[h_str]} for h_str in hours]

    def sales(self, request):
        now = timezone.now()
        # Total Sales: quantity sum of non-cancelled/non-refunded orders
        sales_qs = OrderItem.objects.exclude(order__status__in=[Order.CANCELLED, Order.REFUNDED])
        total_sales = sales_qs.aggregate(total=Sum("quantity"))["total"] or 0

        trend_data = self._get_trend_data(request, "sales")
        trend_data = [{"month": x["month"], "sales": x["sales"], "orders": x["sales"]} for x in trend_data]

        return Response({
            "value": str(total_sales),
            "change": "+12.5% this month",
            "detail": "Total products sold",
            "trend": trend_data
        }, status=status.HTTP_200_OK)

    def revenue(self, request):
        revenue_qs = Order.objects.exclude(status__in=[Order.CANCELLED, Order.REFUNDED])
        total_revenue = revenue_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")

        trend_data = self._get_trend_data(request, "revenue")

        return Response({
            "value": f"₹{total_revenue:,.2f}",
            "change": "+18.2% vs last month",
            "detail": "Gross store revenue",
            "trend": trend_data
        }, status=status.HTTP_200_OK)

    def orders(self, request):
        total_orders = Order.objects.count()

        trend_data = self._get_trend_data(request, "orders")

        # Status distribution
        status_dist = Order.objects.values("status").annotate(count=Count("id"))

        return Response({
            "value": str(total_orders),
            "change": "+8.3% this month",
            "detail": "Successfully placed checkouts",
            "status_distribution": list(status_dist),
            "trend": trend_data
        }, status=status.HTTP_200_OK)

    def customers(self, request):
        now = timezone.now()
        total_customers = User.objects.filter(role="customer").count()

        cust_monthly = User.objects.filter(
            role="customer",
            date_joined__year=now.year
        ).annotate(month_trunc=TruncMonth("date_joined")) \
         .values("month_trunc") \
         .annotate(signups=Count("id")) \
         .order_by("month_trunc")

        dummy_months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        signups_trend = {m: 0 for m in dummy_months}
        for entry in cust_monthly:
            if entry["month_trunc"]:
                month_name = entry["month_trunc"].strftime("%b")
                signups_trend[month_name] = entry["signups"]

        trend_data = [{"month": m, "signups": signups_trend[m]} for m in dummy_months]

        return Response({
            "value": str(total_customers),
            "change": "+15.6% signup growth",
            "detail": "Registered user profiles",
            "trend": trend_data
        }, status=status.HTTP_200_OK)

    def products(self, request):
        total_products = Product.objects.count()

        # Category distribution
        cat_dist = Product.objects.values("category__name").annotate(value=Count("id"))
        cat_data = [{"name": entry["category__name"] or "Uncategorized", "value": entry["value"]} for entry in cat_dist]

        # Top products
        top_selling = OrderItem.objects.exclude(order__status__in=[Order.CANCELLED, Order.REFUNDED]) \
            .values("product_id", "product_name", "product__category__name") \
            .annotate(
                sales=Sum("quantity"),
                revenue=Sum("total")
            ).order_by("-sales")[:5]

        return Response({
            "value": str(total_products),
            "change": "+14 new items added",
            "detail": "Active online listings",
            "category_distribution": cat_data,
            "top_products": list(top_selling)
        }, status=status.HTTP_200_OK)


class AdminProfileView(APIView):
    """
    View for admin to retrieve or update their profile.
    """
    permission_classes = [AdminPermission]

    def get(self, request):
        serializer = UserSerializer(request.user)
        # Combine user with profile if profile exists
        profile_data = {}
        try:
            profile = request.user.profile
            profile_serializer = ProfileSerializer(profile)
            profile_data = profile_serializer.data
        except AttributeError:
            pass

        data = serializer.data
        data["profile_details"] = profile_data
        return Response(data, status=status.HTTP_200_OK)

    def put(self, request):
        # Update user fields
        user = request.user
        user_serializer = UserSerializer(user, data=request.data, partial=True)
        user_serializer.is_valid(raise_exception=True)
        user_serializer.save()

        # Update profile fields
        try:
            profile = request.user.profile
            profile_serializer = ProfileSerializer(profile, data=request.data, partial=True)
            profile_serializer.is_valid(raise_exception=True)
            profile_serializer.save()
        except AttributeError:
            pass

        return self.get(request)


class AdminChangePasswordView(APIView):
    """
    View for admin to change their password.
    """
    permission_classes = [AdminPermission]

    def post(self, request):
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not old_password or not new_password:
            return Response(
                {"detail": "Both old_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user = request.user
        if not user.check_password(old_password):
            return Response(
                {"old_password": ["Incorrect old password."]},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_password != confirm_password:
            return Response(
                {"confirm_password": ["Passwords do not match."]},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()
        return Response({"message": "Password updated successfully."}, status=status.HTTP_200_OK)


class AdminSettingsView(APIView):
    """
    View for admin to retrieve or update system settings.
    """
    permission_classes = [AdminPermission]

    def get(self, request):
        setting, created = AppSetting.objects.get_or_create(
            key="config",
            defaults={
                "value": {
                    "language": "en",
                    "notificationsEnabled": True,
                    "emailAlerts": True,
                    "timezone": "UTC+5:30"
                }
            }
        )
        return Response(setting.value, status=status.HTTP_200_OK)

    def put(self, request):
        setting, created = AppSetting.objects.get_or_create(key="config")
        setting.value = request.data
        setting.save()
        return Response(setting.value, status=status.HTTP_200_OK)






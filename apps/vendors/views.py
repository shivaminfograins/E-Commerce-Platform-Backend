import os
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse, Http404
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied

from apps.admin_panel.permissions import AdminPermission
from .models import Vendor, VendorAddress, VendorDocument, VendorBankAccount, VendorProductVariant, VendorProductVariantImage, VendorProductRequest
from .serializers import (
    VendorSerializer,
    VendorAddressSerializer,
    VendorBankAccountSerializer,
    VendorDocumentSerializer,
    VendorStoreProfileUpdateSerializer,
    VendorProductSerializer,
    VendorProductVariantSerializer,
    VendorProductVariantImageSerializer,
    VendorProductRequestSerializer
)
from .permissions import IsVendor, IsApprovedVendor

User = get_user_model()


from rest_framework_simplejwt.authentication import JWTAuthentication

class QueryParamJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        header_auth = super().authenticate(request)
        if header_auth is not None:
            return header_auth
        
        token = request.query_params.get("token")
        if not token:
            return None
            
        try:
            validated_token = self.get_validated_token(token)
            return self.get_user(validated_token), validated_token
        except Exception:
            return None


class VendorDocumentDownloadView(APIView):
    authentication_classes = [QueryParamJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        doc = get_object_or_404(VendorDocument, pk=pk)
        
        # Check permissions: Admin or Owner Vendor
        is_admin = request.user.role == "admin"
        is_owner = hasattr(request.user, "vendor_profile") and request.user.vendor_profile == doc.vendor
        
        if not (is_admin or is_owner):
            raise PermissionDenied("You do not have permission to view this document.")
        
        if not doc.document_file:
            raise Http404("File not found.")
            
        file_path = doc.document_file.path
        if not os.path.exists(file_path):
            raise Http404("File does not exist on disk.")
            
        return FileResponse(open(file_path, 'rb'))


class VendorApplyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if hasattr(request.user, "vendor_profile"):
            return Response(
                {"detail": "You already have a vendor profile. Use the profile endpoint to update or resubmit."},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = request.data

        # Explicitly validate that at least one document is uploaded in request.FILES
        uploaded_doc_keys = [k for k in request.FILES.keys() if k.startswith("document_file_")]
        if not uploaded_doc_keys:
            return Response(
                {"detail": "At least one verification document is required to submit the application."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                # 1. Create Vendor Business Profile
                vendor = Vendor.objects.create(
                    user=request.user,
                    business_name=data.get("business_name"),
                    store_name=data.get("store_name"),
                    business_email=data.get("business_email"),
                    business_phone=data.get("business_phone"),
                    status="PENDING"
                )

                # 2. Create Vendor Address
                address_serializer = VendorAddressSerializer(data={
                    "address_type": data.get("address_type", "BUSINESS"),
                    "full_name": data.get("address_full_name"),
                    "phone": data.get("address_phone"),
                    "address_line_1": data.get("address_line_1"),
                    "address_line_2": data.get("address_line_2", ""),
                    "city": data.get("address_city"),
                    "state": data.get("address_state"),
                    "country": data.get("address_country", "India"),
                    "postal_code": data.get("address_postal_code")
                })
                address_serializer.is_valid(raise_exception=True)
                address_serializer.save(vendor=vendor)

                # 3. Create Vendor Bank Account
                bank_serializer = VendorBankAccountSerializer(data={
                    "account_holder_name": data.get("bank_account_holder_name"),
                    "account_number": data.get("bank_account_number"),
                    "bank_name": data.get("bank_name"),
                    "branch_name": data.get("bank_branch_name"),
                    "ifsc_code": data.get("bank_ifsc_code")
                })
                bank_serializer.is_valid(raise_exception=True)
                bank_serializer.save(vendor=vendor)

                # 4. Create Vendor Documents
                for key in uploaded_doc_keys:
                    doc_type = key.replace("document_file_", "")
                    doc_file = request.FILES.get(key)
                    doc_number = data.get(f"document_number_{doc_type}", "")
                    
                    doc_serializer = VendorDocumentSerializer(data={
                        "document_type": doc_type,
                        "document_file": doc_file,
                        "document_number": doc_number
                    }, context={"request": request})
                    doc_serializer.is_valid(raise_exception=True)
                    doc_serializer.save(vendor=vendor)

                serializer = VendorSerializer(vendor, context={"request": request})
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            # Full database rollback triggers automatically on any exception raised inside atomic block
            raise ValidationError(detail=str(e))


class VendorDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def get(self, request):
        from decimal import Decimal
        from django.db.models import Count, Sum, Q
        from apps.orders.models import VendorOrder
        from apps.settlements.models import VendorSettlement
        from .models import VendorProduct

        vendor = request.user.vendor_profile

        # 1. Product KPI
        products_count = VendorProduct.objects.filter(vendor=vendor).count()

        # 2. Vendor Order stats (single aggregated query)
        order_aggregates = VendorOrder.objects.filter(vendor=vendor).aggregate(
            total_orders=Count("id"),
            pending=Count("id", filter=Q(status=VendorOrder.PENDING)),
            confirmed=Count("id", filter=Q(status=VendorOrder.CONFIRMED)),
            processing=Count("id", filter=Q(status=VendorOrder.PROCESSING)),
            packed=Count("id", filter=Q(status=VendorOrder.PACKED)),
            shipped=Count("id", filter=Q(status=VendorOrder.SHIPPED)),
            delivered=Count("id", filter=Q(status=VendorOrder.DELIVERED)),
        )

        orders_count = order_aggregates["total_orders"] or 0
        action_required_count = (
            (order_aggregates["confirmed"] or 0)
            + (order_aggregates["processing"] or 0)
            + (order_aggregates["packed"] or 0)
        )

        # 3. Inventory Overview
        vendor_products = VendorProduct.objects.filter(vendor=vendor).prefetch_related("vendor_variants")
        total_skus = 0
        in_stock = 0
        low_stock = 0
        out_of_stock = 0

        for vp in vendor_products:
            variants = list(vp.vendor_variants.all())
            if variants:
                for v in variants:
                    total_skus += 1
                    stk = v.stock or 0
                    if stk == 0:
                        out_of_stock += 1
                    elif stk <= 10:
                        low_stock += 1
                    else:
                        in_stock += 1
            else:
                total_skus += 1
                stk = vp.stock or 0
                if stk == 0:
                    out_of_stock += 1
                elif stk <= 10:
                    low_stock += 1
                else:
                    in_stock += 1

        # 4. Actionable Orders Queue (up to 5 oldest)
        action_orders_qs = VendorOrder.objects.filter(
            vendor=vendor,
            status__in=[VendorOrder.CONFIRMED, VendorOrder.PROCESSING, VendorOrder.PACKED]
        ).order_by("created_at")[:5]

        action_orders_list = [
            {
                "id": vo.id,
                "vendor_order_number": vo.vendor_order_number,
                "status": vo.status,
                "total_amount": "{:.2f}".format(vo.total_amount),
                "created_at": vo.created_at.isoformat() if vo.created_at else None,
            }
            for vo in action_orders_qs
        ]

        # 5. Financial / Settlement Summary
        settlements = VendorSettlement.objects.filter(vendor=vendor)

        settled_val = settlements.filter(status=VendorSettlement.SETTLED).aggregate(
            total=Sum("net_payable")
        )["total"] or Decimal("0.00")

        pending_payout_val = settlements.filter(
            status__in=[VendorSettlement.DRAFT, VendorSettlement.PENDING_APPROVAL, VendorSettlement.APPROVED],
            payout_status__in=[
                VendorSettlement.PAYOUT_UNPAID,
                VendorSettlement.PAYOUT_QUEUED,
                VendorSettlement.PAYOUT_PROCESSING,
            ]
        ).aggregate(total=Sum("net_payable"))["total"] or Decimal("0.00")

        commission_val = settlements.filter(status=VendorSettlement.SETTLED).aggregate(
            total=Sum("commission_amount")
        )["total"] or Decimal("0.00")

        settlement_gross = settlements.exclude(status=VendorSettlement.CANCELLED).aggregate(
            total=Sum("gross_amount")
        )["total"] or Decimal("0.00")

        unsettled_delivered_gross = VendorOrder.objects.filter(
            vendor=vendor,
            status=VendorOrder.DELIVERED,
            settlement_item__isnull=True
        ).aggregate(total=Sum("subtotal"))["total"] or Decimal("0.00")

        total_sales_val = settlement_gross + unsettled_delivered_gross

        data = {
            "store_name": vendor.store_name,
            "business_name": vendor.business_name,
            "status": vendor.status,
            "commission_rate": "{:.2f}".format(vendor.commission_rate) if vendor.commission_rate is not None else "0.00",
            "store": {
                "name": vendor.store_name,
                "status": vendor.status,
            },
            "kpis": {
                "products": products_count,
                "orders": orders_count,
                "action_required": action_required_count,
                "net_earnings": "{:.2f}".format(settled_val),
            },
            "orders": {
                "pending": order_aggregates["pending"] or 0,
                "confirmed": order_aggregates["confirmed"] or 0,
                "processing": order_aggregates["processing"] or 0,
                "packed": order_aggregates["packed"] or 0,
                "shipped": order_aggregates["shipped"] or 0,
                "delivered": order_aggregates["delivered"] or 0,
            },
            "inventory": {
                "total_skus": total_skus,
                "in_stock": in_stock,
                "low_stock": low_stock,
                "out_of_stock": out_of_stock,
            },
            "action_orders": action_orders_list,
            "settlement": {
                "total_sales": "{:.2f}".format(total_sales_val),
                "commission": "{:.2f}".format(commission_val),
                "settled": "{:.2f}".format(settled_val),
                "pending_payout": "{:.2f}".format(pending_payout_val),
            },
        }

        return Response(data)


class VendorProfileView(APIView):
    permission_classes = [IsAuthenticated, IsVendor]

    def get(self, request):
        vendor = request.user.vendor_profile
        serializer = VendorSerializer(vendor, context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        # Obtain row lock to avoid write race conditions
        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(user=request.user)

            if vendor.status == "SUSPENDED":
                return Response(
                    {"detail": f"Cannot edit vendor profile when status is {vendor.status}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            data = request.data

            # Select the appropriate serializer based on Vendor.status
            if vendor.status == "APPROVED":
                # Only explicitly allowed fields in VendorStoreProfileUpdateSerializer are updated
                # If forbidden fields are explicitly sent, the serializer raises ValidationError (HTTP 400)
                update_serializer = VendorStoreProfileUpdateSerializer(
                    vendor, data=data, partial=True, context={"request": request}
                )
                update_serializer.is_valid(raise_exception=True)
                update_serializer.save()
            else:
                # PENDING or REJECTED: allowed to edit whole profile (application)
                # Update Vendor business info if provided
                for field in ["business_name", "store_name", "business_email", "business_phone"]:
                    if field in data:
                        setattr(vendor, field, data.get(field))
                vendor.save()

            # Address fields edit validation and handling
            if "address_line_1" in data or "address_city" in data or "address_type" in data:
                address = vendor.addresses.first()
                if address:
                    address_data = {
                        "address_type": data.get("address_type", address.address_type),
                        "full_name": data.get("address_full_name", address.full_name),
                        "phone": data.get("address_phone", address.phone),
                        "address_line_1": data.get("address_line_1", address.address_line_1),
                        "address_line_2": data.get("address_line_2", address.address_line_2),
                        "city": data.get("address_city", address.city),
                        "state": data.get("address_state", address.state),
                        "country": data.get("address_country", address.country),
                        "postal_code": data.get("address_postal_code", address.postal_code)
                    }
                    addr_serializer = VendorAddressSerializer(address, data=address_data, partial=True)
                    addr_serializer.is_valid(raise_exception=True)
                    addr_serializer.save()

            # Bank updating (Only allowed for non-APPROVED applications)
            bank_keys = ["bank_account_holder_name", "bank_account_number", "bank_name", "bank_branch_name", "bank_ifsc_code"]
            if any(k in data for k in bank_keys):
                if vendor.status == "APPROVED":
                    raise ValidationError({"bank_account": "Approved vendors cannot update banking details."})

                bank = getattr(vendor, "bank_account", None)
                if bank:
                    raw_acc_num = data.get("bank_account_number", bank.account_number)
                    # If omitted or masked, preserve existing account number
                    if not raw_acc_num or "*" in raw_acc_num:
                        raw_acc_num = bank.account_number

                    bank_data = {
                        "account_holder_name": data.get("bank_account_holder_name", bank.account_holder_name),
                        "account_number": raw_acc_num,
                        "bank_name": data.get("bank_name", bank.bank_name),
                        "branch_name": data.get("bank_branch_name", bank.branch_name),
                        "ifsc_code": data.get("bank_ifsc_code", bank.ifsc_code)
                    }
                    bank_serializer = VendorBankAccountSerializer(bank, data=bank_data, partial=True)
                    bank_serializer.is_valid(raise_exception=True)
                    bank_serializer.save()

            # Document updating (Only allowed for non-APPROVED applications)
            uploaded_doc_keys = [k for k in request.FILES.keys() if k.startswith("document_file_")]
            if uploaded_doc_keys:
                if vendor.status == "APPROVED":
                    raise ValidationError({"documents": "Approved vendors cannot update verification documents."})

                for key in uploaded_doc_keys:
                    doc_type = key.replace("document_file_", "")
                    doc_file = request.FILES.get(key)
                    doc_number = data.get(f"document_number_{doc_type}", "")

                    # Check if document already exists
                    doc = vendor.documents.filter(document_type=doc_type).first()
                    doc_data = {
                        "document_type": doc_type,
                        "document_file": doc_file,
                        "document_number": doc_number
                    }
                    if doc:
                        doc_serializer = VendorDocumentSerializer(doc, data=doc_data, partial=True, context={"request": request})
                    else:
                        doc_serializer = VendorDocumentSerializer(data=doc_data, context={"request": request})
                    
                    doc_serializer.is_valid(raise_exception=True)
                    doc_serializer.save(vendor=vendor)

            serializer = VendorSerializer(vendor, context={"request": request})
            return Response(serializer.data)



class VendorResubmitView(APIView):
    permission_classes = [IsAuthenticated, IsVendor]

    def post(self, request):
        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(user=request.user)

            if vendor.status != "REJECTED":
                return Response(
                    {"detail": "Only rejected applications can be resubmitted."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Ensure at least one document exists before resubmitting
            if not vendor.documents.exists():
                return Response(
                    {"detail": "Cannot resubmit without uploading verification documents."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            vendor.status = "PENDING"
            vendor.rejection_reason = ""
            vendor.save()

            # Reset document verification statuses to PENDING
            vendor.documents.all().update(verification_status="PENDING", rejection_reason="")

            serializer = VendorSerializer(vendor, context={"request": request})
            return Response(serializer.data)


class AdminVendorViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, AdminPermission]
    queryset = Vendor.objects.all().order_by("-created_at")
    serializer_class = VendorSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        status_param = self.request.query_name = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(business_name__icontains=search) | queryset.filter(store_name__icontains=search)
        return queryset

    @action(detail=True, methods=["post"], url_path="approve")
    def approve_vendor(self, request, pk=None):
        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(pk=pk)

            if vendor.status != "PENDING":
                raise ValidationError("Only PENDING applications can be approved.")

            # Validate required document counts and completeness
            if not vendor.documents.exists():
                raise ValidationError("Cannot approve vendor with no verification documents.")

            # Validate address completeness
            if not vendor.addresses.exists():
                raise ValidationError("Cannot approve vendor without address records.")

            # Validate bank account presence
            if not hasattr(vendor, "bank_account"):
                raise ValidationError("Cannot approve vendor without a bank account record.")

            # Approve Vendor
            vendor.status = "APPROVED"
            vendor.rejection_reason = ""
            vendor.save()

            # Update User Role
            user = vendor.user
            user.role = "vendor"
            user.save()

            # Atomically mark all uploaded documents APPROVED
            vendor.documents.all().update(verification_status="APPROVED")

            return Response(VendorSerializer(vendor, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject_vendor(self, request, pk=None):
        rejection_reason = request.data.get("rejection_reason")
        if not rejection_reason:
            raise ValidationError("Rejection reason is required.")

        with transaction.atomic():
            vendor = Vendor.objects.select_for_update().get(pk=pk)

            if vendor.status != "PENDING":
                raise ValidationError("Only PENDING applications can be rejected.")

            vendor.status = "REJECTED"
            vendor.rejection_reason = rejection_reason
            vendor.save()

            # Reset associated user role to customer just in case
            user = vendor.user
            user.role = "customer"
            user.save()

            # Update document status to REJECTED
            vendor.documents.all().update(verification_status="REJECTED")

            return Response(VendorSerializer(vendor, context={"request": request}).data)


from rest_framework import generics
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from apps.products.models import Product
from .models import VendorProduct, VendorProductVariant
from .serializers import (
    VendorCatalogProductSerializer,
    VendorProductSerializer,
    VendorProductVariantSerializer, VendorProductVariantImage, VendorProductRequestSerializer
)

class VendorCatalogProductListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorCatalogProductSerializer
    queryset = Product.objects.filter(is_active=True).order_by("-created_at")
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["category", "brand"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["-created_at"]


class VendorCatalogProductDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorCatalogProductSerializer
    queryset = Product.objects.filter(is_active=True)
    lookup_field = "pk"


class VendorProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorProductSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["product__name", "product__description", "product__category__name"]
    ordering_fields = ["created_at", "updated_at", "price", "stock"]
    ordering = ["-created_at"]

    def get_queryset(self):
        # Strict ownership protection
        return VendorProduct.objects.filter(vendor=self.request.user.vendor_profile).prefetch_related(
            "vendor_variants", "vendor_variants__product_variant"
        ).select_related("product", "product__category", "product__brand").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(vendor=self.request.user.vendor_profile)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except Exception as e:
            if isinstance(e, DjangoValidationError):
                raise ValidationError({"detail": str(e.message if hasattr(e, 'message') else e)})
            from django.db.models import ProtectedError
            if isinstance(e, ProtectedError):
                raise ValidationError({"detail": "Cannot delete because this listing is protected."})
            raise e


class VendorProductVariantDeleteView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorProductVariantSerializer, VendorProductVariantImage, VendorProductRequestSerializer

    def get_queryset(self):
        # Strict ownership protection: must belong to the requesting vendor's products
        return VendorProductVariant.objects.filter(vendor_product__vendor=self.request.user.vendor_profile)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except Exception as e:
            if isinstance(e, DjangoValidationError):
                raise ValidationError({"detail": str(e.message if hasattr(e, 'message') else e)})
            from django.db.models import ProtectedError
            if isinstance(e, ProtectedError):
                raise ValidationError({"detail": "Cannot delete because this variant is protected."})
            raise e


from rest_framework.generics import ListAPIView, RetrieveAPIView
from .models import VendorInventoryMovement
from .serializers import VendorInventorySerializer, VendorInventoryMovementSerializer
from .services import record_inventory_movement

class VendorInventoryListView(ListAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorInventorySerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["product__name", "product__description"]
    ordering_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return VendorProduct.objects.filter(vendor=self.request.user.vendor_profile).prefetch_related(
            "vendor_variants", "vendor_variants__product_variant"
        ).select_related("product", "product__category", "product__brand")


class VendorInventoryDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorInventorySerializer

    def get_queryset(self):
        return VendorProduct.objects.filter(vendor=self.request.user.vendor_profile)


class VendorInventoryHistoryListView(ListAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorInventoryMovementSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["vendor_product", "vendor_product_variant", "movement_type", "reason"]
    search_fields = ["product_name_snapshot", "variant_name_snapshot", "note"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return VendorInventoryMovement.objects.filter(vendor=self.request.user.vendor_profile).select_related(
            "vendor_product", "vendor_product_variant", "created_by"
        )


class VendorProductInventoryMovementView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def post(self, request, product_id):
        serializer = VendorInventoryMovementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            movement = record_inventory_movement(
                user=request.user,
                vendor_product_id=product_id,
                vendor_product_variant_id=None,
                movement_type=serializer.validated_data["movement_type"],
                quantity=serializer.validated_data["quantity"],
                reason=serializer.validated_data["reason"],
                note=serializer.validated_data.get("note", ""),
                reference_type=serializer.validated_data.get("reference_type"),
                reference_id=serializer.validated_data.get("reference_id")
            )
            return Response(VendorInventoryMovementSerializer(movement).data, status=status.HTTP_201_CREATED)
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else str(e))


class VendorVariantInventoryMovementView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]

    def post(self, request, product_id, variant_id):
        serializer = VendorInventoryMovementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            movement = record_inventory_movement(
                user=request.user,
                vendor_product_id=product_id,
                vendor_product_variant_id=variant_id,
                movement_type=serializer.validated_data["movement_type"],
                quantity=serializer.validated_data["quantity"],
                reason=serializer.validated_data["reason"],
                note=serializer.validated_data.get("note", ""),
                reference_type=serializer.validated_data.get("reference_type"),
                reference_id=serializer.validated_data.get("reference_id")
            )
            return Response(VendorInventoryMovementSerializer(movement).data, status=status.HTTP_201_CREATED)
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else str(e))




class VendorProductVariantImageCreateView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorProductVariantImageSerializer

    def perform_create(self, serializer):
        variant_id = self.kwargs.get("variant_id")
        variant = get_object_or_404(VendorProductVariant, pk=variant_id, vendor_product__vendor=self.request.user.vendor_profile)
        serializer.save(vendor_variant=variant)

class VendorProductVariantImageDeleteView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorProductVariantImageSerializer

    def get_queryset(self):
        return VendorProductVariantImage.objects.filter(vendor_variant__vendor_product__vendor=self.request.user.vendor_profile)


class VendorProductRequestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    serializer_class = VendorProductRequestSerializer

    def get_queryset(self):
        return VendorProductRequest.objects.filter(vendor=self.request.user.vendor_profile)

    def perform_create(self, serializer):
        serializer.save(vendor=self.request.user.vendor_profile)

import os
from django.test import TestCase
from django.db.utils import IntegrityError
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Vendor, VendorAddress, VendorDocument, VendorBankAccount
from .permissions import IsVendor, IsApprovedVendor

User = get_user_model()


class DummyVendorView(APIView):
    permission_classes = [IsAuthenticated, IsVendor]
    def get(self, request):
        return Response({"status": "vendor_only"})


class DummyApprovedVendorView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedVendor]
    def get(self, request):
        return Response({"status": "approved_vendor_only"})


class VendorModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="securepassword123"
        )

    def test_user_exists_without_vendor_profile(self):
        """
        1. User can exist without a Vendor profile.
        """
        self.assertFalse(hasattr(self.user, 'vendor_profile'))

    def test_vendor_correctly_linked_to_one_user(self):
        """
        2. Vendor is correctly linked to one User.
        """
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Test Legal Entity LLC",
            store_name="Super Store",
            business_email="contact@superstore.com",
            business_phone="1234567890",
            status="PENDING"
        )
        self.assertEqual(vendor.user, self.user)
        self.assertEqual(self.user.vendor_profile, vendor)

    def test_user_cannot_own_multiple_vendor_profiles(self):
        """
        3. One User cannot own multiple OneToOne Vendor profiles.
        """
        Vendor.objects.create(
            user=self.user,
            business_name="First Store",
            store_name="First",
            business_email="first@store.com",
            business_phone="1234567890"
        )
        with self.assertRaises(IntegrityError):
            Vendor.objects.create(
                user=self.user,
                business_name="Second Store",
                store_name="Second",
                business_email="second@store.com",
                business_phone="9876543210"
            )


class VendorPermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        
        # Create normal user
        self.normal_user = User.objects.create_user(
            username="normaluser",
            email="normal@example.com",
            password="password123"
        )
        
        # Create user who is a pending vendor
        self.pending_user = User.objects.create_user(
            username="pendinguser",
            email="pending@example.com",
            password="password123"
        )
        self.pending_vendor = Vendor.objects.create(
            user=self.pending_user,
            business_name="Pending LLC",
            store_name="Pending Store",
            business_email="pending@store.com",
            business_phone="1234567890",
            status="PENDING"
        )
        
        # Create user who is an approved vendor
        self.approved_user = User.objects.create_user(
            username="approveduser",
            email="approved@example.com",
            password="password123"
        )
        self.approved_vendor = Vendor.objects.create(
            user=self.approved_user,
            business_name="Approved LLC",
            store_name="Approved Store",
            business_email="approved@store.com",
            business_phone="1234567890",
            status="APPROVED"
        )

        # Create user who is a rejected vendor
        self.rejected_user = User.objects.create_user(
            username="rejecteduser",
            email="rejected@example.com",
            password="password123"
        )
        self.rejected_vendor = Vendor.objects.create(
            user=self.rejected_user,
            business_name="Rejected LLC",
            store_name="Rejected Store",
            business_email="rejected@store.com",
            business_phone="1234567890",
            status="REJECTED"
        )

        # Create user who is a suspended vendor
        self.suspended_user = User.objects.create_user(
            username="suspendeduser",
            email="suspended@example.com",
            password="password123"
        )
        self.suspended_vendor = Vendor.objects.create(
            user=self.suspended_user,
            business_name="Suspended LLC",
            store_name="Suspended Store",
            business_email="suspended@store.com",
            business_phone="1234567890",
            status="SUSPENDED"
        )

    def test_is_vendor_permission(self):
        """
        4. IsVendor allows valid vendor users.
        5. IsVendor denies normal users.
        """
        view = DummyVendorView.as_view()

        # Deny unauthenticated
        request = self.factory.get('/dummy/')
        response = view(request)
        self.assertEqual(response.status_code, 401)

        # Deny normal authenticated user without vendor profile
        request = self.factory.get('/dummy/')
        force_authenticate(request, user=self.normal_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Allow vendor users (any status)
        for user in [self.pending_user, self.approved_user, self.rejected_user, self.suspended_user]:
            request = self.factory.get('/dummy/')
            force_authenticate(request, user=user)
            response = view(request)
            self.assertEqual(response.status_code, 200)

    def test_is_approved_vendor_permission(self):
        """
        6. IsApprovedVendor allows APPROVED vendor.
        7. IsApprovedVendor denies PENDING vendor.
        8. IsApprovedVendor denies REJECTED vendor.
        9. IsApprovedVendor denies SUSPENDED vendor.
        """
        view = DummyApprovedVendorView.as_view()

        # Deny normal user
        request = self.factory.get('/dummy-approved/')
        force_authenticate(request, user=self.normal_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Deny pending vendor
        request = self.factory.get('/dummy-approved/')
        force_authenticate(request, user=self.pending_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Deny rejected vendor
        request = self.factory.get('/dummy-approved/')
        force_authenticate(request, user=self.rejected_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Deny suspended vendor
        request = self.factory.get('/dummy-approved/')
        force_authenticate(request, user=self.suspended_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Allow approved vendor
        request = self.factory.get('/dummy-approved/')
        force_authenticate(request, user=self.approved_user)
        response = view(request)
        self.assertEqual(response.status_code, 200)


class VendorRelatedModelsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vendorowner",
            email="owner@example.com",
            password="password123"
        )
        self.vendor = Vendor.objects.create(
            user=self.user,
            business_name="Owner LLC",
            store_name="Owner Store",
            business_email="owner@store.com",
            business_phone="1234567890",
            status="APPROVED"
        )

    def test_vendor_address_fields(self):
        address = VendorAddress.objects.create(
            vendor=self.vendor,
            address_type="WAREHOUSE",
            full_name="Hub W1",
            phone="12345",
            address_line_1="10 Sector",
            city="Delhi",
            state="Delhi",
            postal_code="110001"
        )
        self.assertEqual(address.vendor, self.vendor)
        self.assertEqual(address.address_type, "WAREHOUSE")
        self.assertEqual(address.country, "India")

    def test_vendor_document_fields(self):
        document = VendorDocument.objects.create(
            vendor=self.vendor,
            document_type="GSTIN",
            document_number="07AAAAA1111A1Z1",
            document_file="temp.pdf",
            verification_status="PENDING"
        )
        self.assertEqual(document.vendor, self.vendor)
        self.assertEqual(document.document_number, "07AAAAA1111A1Z1")
        self.assertEqual(document.verification_status, "PENDING")

    def test_vendor_bank_account_fields(self):
        bank = VendorBankAccount.objects.create(
            vendor=self.vendor,
            account_holder_name="Owner LLC",
            account_number="123456789012",
            bank_name="State Bank",
            branch_name="Delhi Main",
            ifsc_code="SBIN0000001"
        )
        self.assertEqual(bank.vendor, self.vendor)
        self.assertEqual(bank.account_number, "123456789012")


class VendorWorkflowTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="candidate",
            email="candidate@example.com",
            password="password123"
        )
        self.admin_user = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="password123",
            role="admin"
        )
        self.other_user = User.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="password123"
        )

    def test_failed_creation_atomic_rollback(self):
        """
        Verify transaction rollback during failed application creation.
        If nested model creation fails (e.g. invalid phone number/IFSC),
        no partial Vendor profile should persist in the database.
        """
        from apps.vendors.views import VendorApplyView
        view = VendorApplyView.as_view()
        
        # Open dummy files
        import io
        dummy_file = io.BytesIO(b"file content")
        dummy_file.name = "pan.pdf"

        # Missing required address fields makes address validation fail
        data = {
            "business_name": "Rollback Corp",
            "store_name": "Rollback Store",
            "business_email": "rollback@corp.com",
            "business_phone": "12345",
            # missing address state, city, etc.
            "bank_account_holder_name": "Rollback Corp",
            "bank_account_number": "12345",
            "bank_name": "Test Bank",
            "bank_branch_name": "Test Branch",
            "bank_ifsc_code": "TEST0000001",
            "document_file_PAN": dummy_file,
        }
        
        request = self.factory.post('/api/vendors/apply/', data, format='multipart')
        force_authenticate(request, user=self.user)
        response = view(request)
        
        # Verification fails, returning 400
        self.assertEqual(response.status_code, 400)
        # Check that no Vendor object was saved to the db
        self.assertFalse(Vendor.objects.filter(user=self.user).exists())

    def test_approval_updates_status_and_role(self):
        """
        Approval atomically updates Vendor.status to APPROVED and User.role to vendor.
        """
        from apps.vendors.views import AdminVendorViewSet
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Approve LLC",
            store_name="Approve Store",
            business_email="approve@llc.com",
            business_phone="12345"
        )
        VendorAddress.objects.create(
            vendor=vendor,
            full_name="Address",
            phone="123",
            address_line_1="Street",
            city="Delhi",
            state="Delhi",
            postal_code="110001"
        )
        VendorBankAccount.objects.create(
            vendor=vendor,
            account_holder_name="Approve LLC",
            account_number="12345",
            bank_name="Test Bank",
            branch_name="Test Branch",
            ifsc_code="TEST0000001"
        )
        VendorDocument.objects.create(
            vendor=vendor,
            document_type="PAN",
            document_number="PAN123"
        )

        view = AdminVendorViewSet.as_view({"post": "approve_vendor"})
        request = self.factory.post(f'/api/admin/vendors/{vendor.id}/approve/')
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=vendor.id)
        
        self.assertEqual(response.status_code, 200)
        vendor.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(vendor.status, "APPROVED")
        self.assertEqual(self.user.role, "vendor")

    def test_rejection_keeps_customer_role(self):
        """
        Rejection keeps User.role as customer.
        """
        from apps.vendors.views import AdminVendorViewSet
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Reject LLC",
            store_name="Reject Store",
            business_email="reject@llc.com",
            business_phone="12345"
        )
        view = AdminVendorViewSet.as_view({"post": "reject_vendor"})
        request = self.factory.post(f'/api/admin/vendors/{vendor.id}/reject/', {"rejection_reason": "Missing documents"})
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=vendor.id)

        self.assertEqual(response.status_code, 200)
        vendor.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(vendor.status, "REJECTED")
        self.assertEqual(vendor.rejection_reason, "Missing documents")
        self.assertEqual(self.user.role, "customer")

    def test_resubmission_resets_status_and_reason(self):
        """
        Resubmission resets status/rejection_reason while User.role remains customer.
        """
        from apps.vendors.views import VendorResubmitView
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Resubmit LLC",
            store_name="Resubmit Store",
            business_email="resubmit@llc.com",
            business_phone="12345",
            status="REJECTED",
            rejection_reason="Invalid Document"
        )
        VendorDocument.objects.create(
            vendor=vendor,
            document_type="PAN",
            document_number="PAN123"
        )
        view = VendorResubmitView.as_view()
        request = self.factory.post('/api/vendors/profile/resubmit/')
        force_authenticate(request, user=self.user)
        response = view(request)

        self.assertEqual(response.status_code, 200)
        vendor.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(vendor.status, "PENDING")
        self.assertEqual(vendor.rejection_reason, "")
        self.assertEqual(self.user.role, "customer")

    def test_secure_document_access_ownership_matrix(self):
        """
        Verify document download permission constraints:
        - Admin has access.
        - Owner Vendor has access.
        - Other vendors or customers are denied access.
        """
        from apps.vendors.views import VendorDocumentDownloadView
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Doc LLC",
            store_name="Doc Store",
            business_email="doc@llc.com",
            business_phone="12345"
        )
        from django.core.files.base import ContentFile
        doc = VendorDocument.objects.create(
            vendor=vendor,
            document_type="PAN",
            document_number="PAN123"
        )
        doc.document_file.save("pan.pdf", ContentFile("dummy pdf data"))

        view = VendorDocumentDownloadView.as_view()

        # 1. Admin Access -> Allowed
        request = self.factory.get(f'/api/vendors/documents/{doc.id}/download/')
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=doc.id)
        self.assertEqual(response.status_code, 200)
        response.close()

        # 2. Owner Access -> Allowed
        request = self.factory.get(f'/api/vendors/documents/{doc.id}/download/')
        force_authenticate(request, user=self.user)
        response = view(request, pk=doc.id)
        self.assertEqual(response.status_code, 200)
        response.close()

        # 3. Other Authenticated Customer Access -> Denied (403)
        request = self.factory.get(f'/api/vendors/documents/{doc.id}/download/')
        force_authenticate(request, user=self.other_user)
        response = view(request, pk=doc.id)
        self.assertEqual(response.status_code, 403)
        response.close()

        # Clean up file
        if doc.document_file and os.path.exists(doc.document_file.path):
            os.remove(doc.document_file.path)

    def test_required_document_validation_before_approval(self):
        """
        Validation fails if trying to approve a vendor without at least one document.
        """
        from apps.vendors.views import AdminVendorViewSet
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="NoDoc LLC",
            store_name="NoDoc Store",
            business_email="nodoc@llc.com",
            business_phone="12345"
        )
        VendorAddress.objects.create(
            vendor=vendor,
            full_name="Address",
            phone="123",
            address_line_1="Street",
            city="Delhi",
            state="Delhi",
            postal_code="110001"
        )
        VendorBankAccount.objects.create(
            vendor=vendor,
            account_holder_name="NoDoc LLC",
            account_number="12345",
            bank_name="Test Bank",
            branch_name="Test Branch",
            ifsc_code="TEST0000001"
        )

        view = AdminVendorViewSet.as_view({"post": "approve_vendor"})
        request = self.factory.post(f'/api/admin/vendors/{vendor.id}/approve/')
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=vendor.id)
        self.assertEqual(response.status_code, 400)

    def test_raw_media_url_is_not_exposed_in_serializers(self):
        """
        Document file path or direct media folder path must never be exposed.
        Only the secure download URL is exposed.
        """
        from apps.vendors.serializers import VendorDocumentSerializer
        vendor = Vendor.objects.create(
            user=self.user,
            business_name="Exposure LLC",
            store_name="Exposure Store",
            business_email="exposure@llc.com",
            business_phone="12345"
        )
        doc = VendorDocument.objects.create(
            vendor=vendor,
            document_type="PAN",
            document_number="PAN123",
            document_file="vendor_documents/pan.pdf"
        )
        
        serializer = VendorDocumentSerializer(doc)
        data = serializer.data
        # Raw file link should not be present
        self.assertNotIn("document_file", data)
        # Secure download url must be present
        self.assertIn("download_url", data)
        self.assertIn("/api/vendors/documents/", data["download_url"])


class VendorDashboardAndProfileUpdateTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        
        # Approved Vendor
        self.approved_user = User.objects.create_user(
            username="approvedvend", email="appvend@example.com", password="password123"
        )
        self.approved_vendor = Vendor.objects.create(
            user=self.approved_user,
            business_name="Approved Company LLC",
            store_name="Approved Vendor Store",
            business_email="approved@company.com",
            business_phone="9998887770",
            status="APPROVED",
            commission_rate=8.50
        )
        
        # Address for approved vendor
        self.address = VendorAddress.objects.create(
            vendor=self.approved_vendor,
            address_type="BUSINESS",
            full_name="Approved Seller",
            phone="9998887770",
            address_line_1="123 Vendor Lane",
            city="Seller City",
            state="Seller State",
            country="India",
            postal_code="400001"
        )
        
        # Bank account for approved vendor
        self.bank = VendorBankAccount.objects.create(
            vendor=self.approved_vendor,
            account_holder_name="Approved Company LLC",
            account_number="9876543210",
            bank_name="Vendor Bank",
            branch_name="Main Branch",
            ifsc_code="VEND0000123"
        )

        # Suspended Vendor
        self.suspended_user = User.objects.create_user(
            username="suspvend", email="suspvend@example.com", password="password123"
        )
        self.suspended_vendor = Vendor.objects.create(
            user=self.suspended_user,
            business_name="Suspended Company",
            store_name="Suspended Store",
            business_email="suspended@company.com",
            business_phone="8887776660",
            status="SUSPENDED"
        )

        # Pending Vendor
        self.pending_user = User.objects.create_user(
            username="pendingvend", email="pendingvend@example.com", password="password123"
        )
        self.pending_vendor = Vendor.objects.create(
            user=self.pending_user,
            business_name="Pending Company",
            store_name="Pending Store",
            business_email="pending@company.com",
            business_phone="7776665550",
            status="PENDING"
        )

    def test_dashboard_endpoint_access(self):
        from apps.vendors.views import VendorDashboardView
        view = VendorDashboardView.as_view()

        # Unauthenticated
        request = self.factory.get('/api/vendors/dashboard/')
        response = view(request)
        self.assertEqual(response.status_code, 401)

        # Pending Vendor - Denied
        request = self.factory.get('/api/vendors/dashboard/')
        force_authenticate(request, user=self.pending_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Suspended Vendor - Denied
        request = self.factory.get('/api/vendors/dashboard/')
        force_authenticate(request, user=self.suspended_user)
        response = view(request)
        self.assertEqual(response.status_code, 403)

        # Approved Vendor - Allowed
        request = self.factory.get('/api/vendors/dashboard/')
        force_authenticate(request, user=self.approved_user)
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["store_name"], "Approved Vendor Store")
        self.assertEqual(response.data["business_name"], "Approved Company LLC")
        self.assertEqual(response.data["status"], "APPROVED")
        self.assertEqual(response.data["commission_rate"], "8.50")

    def test_approved_vendor_profile_patch_success(self):
        from apps.vendors.views import VendorProfileView
        view = VendorProfileView.as_view()

        request = self.factory.patch('/api/vendors/profile/', {
            "store_name": "Newly Named Store",
            "business_email": "newemail@company.com",
            "address_line_1": "456 New Lane"
        }, format='json')
        force_authenticate(request, user=self.approved_user)
        response = view(request)
        
        self.assertEqual(response.status_code, 200)
        self.approved_vendor.refresh_from_db()
        self.assertEqual(self.approved_vendor.store_name, "Newly Named Store")
        self.assertEqual(self.approved_vendor.business_email, "newemail@company.com")
        self.assertEqual(self.approved_vendor.addresses.first().address_line_1, "456 New Lane")

    def test_approved_vendor_profile_patch_forbidden_fields(self):
        from apps.vendors.views import VendorProfileView
        view = VendorProfileView.as_view()

        forbidden_payloads = [
            {"status": "PENDING"},
            {"commission_rate": "5.00"},
            {"rejection_reason": "Hack attempt"},
            {"bank_account_number": "1111111111"},
            {"bank_name": "Hacked Bank"},
        ]

        for payload in forbidden_payloads:
            request = self.factory.patch('/api/vendors/profile/', payload, format='json')
            force_authenticate(request, user=self.approved_user)
            response = view(request)
            self.assertEqual(response.status_code, 400)
            
            # Verify DB remains unchanged
            self.approved_vendor.refresh_from_db()
            self.assertEqual(self.approved_vendor.status, "APPROVED")
            self.assertEqual(float(self.approved_vendor.commission_rate), 8.50)
            self.assertEqual(self.approved_vendor.bank_account.account_number, "9876543210")


from apps.products.models import Category, Product, ProductVariant
from apps.vendors.models import VendorProduct, VendorProductVariant
from django.core.exceptions import ValidationError
from django.db import IntegrityError

class VendorProductListingTests(TestCase):
    def setUp(self):
        # Create users
        self.user1 = User.objects.create_user(
            username="vendor1", email="vendor1@example.com", password="password123"
        )
        self.user2 = User.objects.create_user(
            username="vendor2", email="vendor2@example.com", password="password123"
        )
        # Create approved vendors
        self.vendor1 = Vendor.objects.create(
            user=self.user1,
            business_name="Vendor One LLC",
            store_name="Store One",
            business_email="v1@example.com",
            business_phone="1112223330",
            status="APPROVED"
        )
        self.vendor2 = Vendor.objects.create(
            user=self.user2,
            business_name="Vendor Two LLC",
            store_name="Store Two",
            business_email="v2@example.com",
            business_phone="4445556660",
            status="APPROVED"
        )
        # Create category
        self.category = Category.objects.create(name="Books", slug="books")
        
        # Product without variants
        self.product_no_var = Product.objects.create(
            category=self.category,
            name="Book Without Variants",
            slug="book-no-var",
            description="A generic book"
        )
        
        # Product with variants
        self.product_with_var = Product.objects.create(
            category=self.category,
            name="Book With Variants",
            slug="book-with-var",
            description="Book available in hardcover and softcover"
        )
        self.variant1 = ProductVariant.objects.create(
            product=self.product_with_var,
            name="Hardcover",
            sku="BOOK-HARDCOVER",
            price=29.99,
            stock=100
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product_with_var,
            name="Softcover",
            sku="BOOK-SOFTCOVER",
            price=14.99,
            stock=200
        )

        # Another separate product and variant
        self.other_product = Product.objects.create(
            category=self.category,
            name="Other Book",
            slug="other-book",
            description="Other book description"
        )
        self.other_variant = ProductVariant.objects.create(
            product=self.other_product,
            name="Default Other",
            sku="OTHER-DEFAULT",
            price=9.99,
            stock=50
        )

    def test_multiple_vendors_can_list_same_product(self):
        # Vendor 1 listings
        vp1 = VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_no_var,
            price=12.99,
            stock=10
        )
        # Vendor 2 listings
        vp2 = VendorProduct.objects.create(
            vendor=self.vendor2,
            product=self.product_no_var,
            price=13.50,
            stock=15
        )
        self.assertEqual(VendorProduct.objects.filter(product=self.product_no_var).count(), 2)

    def test_same_vendor_cannot_duplicate_product_listing(self):
        VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_no_var,
            price=12.99,
            stock=10
        )
        # Creating same vendor+product listing should raise IntegrityError
        with self.assertRaises(IntegrityError):
            VendorProduct.objects.create(
                vendor=self.vendor1,
                product=self.product_no_var,
                price=13.99,
                stock=5
            )

    def test_duplicate_vendor_product_variant_offers_rejected(self):
        vp = VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=None,
            stock=None
        )
        vpv1 = VendorProductVariant.objects.create(
            vendor_product=vp,
            product_variant=self.variant1,
            price=25.00,
            stock=50
        )
        # Duplicating the same variant offer for the same VendorProduct listing should raise IntegrityError
        with self.assertRaises(IntegrityError):
            VendorProductVariant.objects.create(
                vendor_product=vp,
                product_variant=self.variant1,
                price=27.00,
                stock=10
            )

    def test_cross_product_variant_linking_rejected(self):
        vp = VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=None,
            stock=None
        )
        # Try to link variant belonging to other_product to vp (which is for product_with_var)
        vpv = VendorProductVariant(
            vendor_product=vp,
            product_variant=self.other_variant,
            price=10.00,
            stock=5
        )
        with self.assertRaises(ValidationError):
            vpv.full_clean()

    def test_no_variant_product_requires_price_and_stock(self):
        vp = VendorProduct(
            vendor=self.vendor1,
            product=self.product_no_var,
            status="ACTIVE",
            price=None,
            stock=None
        )
        # price and stock are required if no variants exist
        with self.assertRaises(ValidationError):
            vp.full_clean()

        vp.price = 10.00
        # price is set, but stock is missing
        with self.assertRaises(ValidationError):
            vp.full_clean()

        vp.stock = 5
        # both set, should pass
        vp.full_clean()
        vp.save()

    def test_variant_product_requires_vendor_product_price_and_stock_to_be_null(self):
        vp = VendorProduct(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=10.00,
            stock=5
        )
        # price and stock must be NULL if variants exist
        with self.assertRaises(ValidationError):
            vp.full_clean()

        vp.price = None
        with self.assertRaises(ValidationError):
            vp.full_clean()

        vp.stock = None
        # both null, should pass
        vp.full_clean()
        vp.save()

    def test_variant_product_stores_selling_data_in_vendor_product_variant(self):
        vp = VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=None,
            stock=None
        )
        vpv = VendorProductVariant.objects.create(
            vendor_product=vp,
            product_variant=self.variant1,
            price=28.00,
            stock=12
        )
        self.assertEqual(vpv.price, 28.00)
        self.assertEqual(vpv.stock, 12)

    def test_valid_same_product_variant_listing(self):
        vp = VendorProduct.objects.create(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=None,
            stock=None
        )
        # Creating multiple distinct variants under the same VendorProduct is allowed
        vpv1 = VendorProductVariant.objects.create(
            vendor_product=vp,
            product_variant=self.variant1,
            price=28.00,
            stock=12
        )
        vpv2 = VendorProductVariant.objects.create(
            vendor_product=vp,
            product_variant=self.variant2,
            price=13.00,
            stock=20
        )
        vpv1.full_clean()
        vpv2.full_clean()
        self.assertEqual(vp.vendor_variants.count(), 2)

    def test_variant_product_cannot_store_price_stock_at_both_levels(self):
        vp = VendorProduct(
            vendor=self.vendor1,
            product=self.product_with_var,
            price=15.00,
            stock=10
        )
        # Should raise ValidationError at VendorProduct level
        with self.assertRaises(ValidationError):
            vp.full_clean()

    def test_existing_product_apis_and_relationships_unchanged(self):
        # Ensure existing public endpoint still works
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)

    def test_existing_cart_behavior_unchanged(self):
        # Create normal user
        cart_user = User.objects.create_user(
            username="cartuser", email="cartuser@example.com", password="password123"
        )
        # Login
        self.client.login(email="cartuser@example.com", password="password123")
        # Ensure cart list works
        response = self.client.get("/api/cart/")
        self.assertEqual(response.status_code, 200)


from rest_framework.test import APITestCase
from django.urls import reverse

class VendorProductCRUDTests(APITestCase):
    def setUp(self):
        # Users
        self.customer_user = User.objects.create_user(
            username="customer", email="customer@example.com", password="password123"
        )
        self.pending_user = User.objects.create_user(
            username="pendingvend", email="pending@example.com", password="password123"
        )
        self.pending_vendor = Vendor.objects.create(
            user=self.pending_user, business_name="Pending store", store_name="P1",
            business_email="p1@example.com", business_phone="123", status="PENDING"
        )
        self.approved_user_a = User.objects.create_user(
            username="vendor_a", email="vendor_a@example.com", password="password123"
        )
        self.vendor_a = Vendor.objects.create(
            user=self.approved_user_a, business_name="Vendor A store", store_name="A1",
            business_email="a1@example.com", business_phone="456", status="APPROVED"
        )
        self.approved_user_b = User.objects.create_user(
            username="vendor_b", email="vendor_b@example.com", password="password123"
        )
        self.vendor_b = Vendor.objects.create(
            user=self.approved_user_b, business_name="Vendor B store", store_name="B1",
            business_email="b1@example.com", business_phone="789", status="APPROVED"
        )
        self.suspended_user = User.objects.create_user(
            username="susp", email="susp@example.com", password="password123"
        )
        self.suspended_vendor = Vendor.objects.create(
            user=self.suspended_user, business_name="Suspended store", store_name="S1",
            business_email="s1@example.com", business_phone="101", status="SUSPENDED"
        )

        # Products
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product_no_var = Product.objects.create(
            category=self.category, name="Phone Base", slug="phone-base", description="Base phone model"
        )
        self.product_with_var = Product.objects.create(
            category=self.category, name="Phone Premium", slug="phone-prem", description="Premium phone model"
        )
        self.variant1 = ProductVariant.objects.create(
            product=self.product_with_var, name="Red / 128GB", sku="RED-128", price=999.00, stock=50
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product_with_var, name="Blue / 256GB", sku="BLUE-256", price=1199.00, stock=30
        )
        self.other_product = Product.objects.create(
            category=self.category, name="Other Book", slug="other-book", description="Other book description"
        )
        self.other_variant = ProductVariant.objects.create(
            product=self.other_product, name="Default Other", sku="OTHER-DEFAULT", price=9.99, stock=50
        )

    def test_catalog_browsing_authorization(self):
        url = reverse("vendor-catalog-product-list")
        
        # Guest -> blocked
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

        # Customer -> blocked
        self.client.force_authenticate(user=self.customer_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Pending -> blocked
        self.client.force_authenticate(user=self.pending_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Suspended -> blocked
        self.client.force_authenticate(user=self.suspended_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Approved -> allowed
        self.client.force_authenticate(user=self.approved_user_a)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_catalog_meta_and_searching(self):
        self.client.force_authenticate(user=self.approved_user_a)
        # Create a listing for vendor A to check already_listed
        VendorProduct.objects.create(
            vendor=self.vendor_a, product=self.product_no_var, status="DRAFT"
        )
        
        url = reverse("vendor-catalog-product-list")
        response = self.client.get(url, {"search": "Premium"})
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results") if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Phone Premium")
        self.assertTrue(results[0]["has_variants"])

        # Fetch list containing base product (listed by vendor A)
        response = self.client.get(url, {"search": "Base"})
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results") if isinstance(response.data, dict) else response.data
        self.assertTrue(results[0]["already_listed"])
        self.assertFalse(results[0]["has_variants"])

    def test_ownership_scoping(self):
        # Create listing owned by Vendor B
        vp_b = VendorProduct.objects.create(
            vendor=self.vendor_b, product=self.product_no_var, status="ACTIVE", price=500.00, stock=10
        )
        
        # Log in as Vendor A, try to access Vendor B's listing
        self.client.force_authenticate(user=self.approved_user_a)
        detail_url = reverse("vendor-product-detail", kwargs={"pk": vp_b.id})
        
        # GET -> 404
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 404)

        # PATCH -> 404
        response = self.client.patch(detail_url, {"price": 600.00})
        self.assertEqual(response.status_code, 404)

        # DELETE -> 404
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 404)

    def test_non_variant_product_draft_and_active_validation(self):
        self.client.force_authenticate(user=self.approved_user_a)
        create_url = reverse("vendor-product-list")

        # 1. Draft can be incomplete (price/stock null)
        data = {
            "product": self.product_no_var.id,
            "status": "DRAFT",
            "price": None,
            "stock": None
        }
        response = self.client.post(create_url, data, format="json")
        self.assertEqual(response.status_code, 201)
        vp_id = response.data["id"]

        # 2. Try to activate with missing price/stock -> Fail (400)
        detail_url = reverse("vendor-product-detail", kwargs={"pk": vp_id})
        response = self.client.patch(detail_url, {"status": "ACTIVE"}, format="json")
        self.assertEqual(response.status_code, 400)

        # 3. Fill details and activate -> Success
        response = self.client.patch(detail_url, {"status": "ACTIVE", "price": 49.99, "stock": 100}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ACTIVE")

    def test_variant_product_active_validations(self):
        self.client.force_authenticate(user=self.approved_user_a)
        create_url = reverse("vendor-product-list")

        # 1. Try to create active listing without variant offers -> Fail (400)
        data = {
            "product": self.product_with_var.id,
            "status": "ACTIVE",
            "vendor_variants": []
        }
        response = self.client.post(create_url, data, format="json")
        self.assertEqual(response.status_code, 400)

        # 2. Create Draft with incomplete variants data -> Allowed
        data["status"] = "DRAFT"
        response = self.client.post(create_url, data, format="json")
        self.assertEqual(response.status_code, 201)
        vp_id = response.data["id"]

        # 3. Try to activate with inactive variants -> Fail
        detail_url = reverse("vendor-product-detail", kwargs={"pk": vp_id})
        patch_data = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.variant1.id,
                    "price": 950.00,
                    "stock": 10,
                    "is_active": False
                }
            ]
        }
        response = self.client.patch(detail_url, patch_data, format="json")
        self.assertEqual(response.status_code, 400)

        # 4. Activate with valid active variants -> Success
        patch_data["vendor_variants"][0]["is_active"] = True
        response = self.client.patch(detail_url, patch_data, format="json")
        self.assertEqual(response.status_code, 200)

    def test_suspended_listing_locked(self):
        # Create suspended listing directly
        vp = VendorProduct.objects.create(
            vendor=self.vendor_a, product=self.product_no_var, status="SUSPENDED"
        )
        self.client.force_authenticate(user=self.approved_user_a)
        detail_url = reverse("vendor-product-detail", kwargs={"pk": vp.id})

        # Try to modify -> Fail (400)
        response = self.client.patch(detail_url, {"status": "ACTIVE", "price": 10.00, "stock": 5}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_patch_variants_preservation(self):
        # Create listing with variants 1 and 2
        vp = VendorProduct.objects.create(
            vendor=self.vendor_a, product=self.product_with_var, status="DRAFT"
        )
        vpv1 = VendorProductVariant.objects.create(
            vendor_product=vp, product_variant=self.variant1, price=900.00, stock=5, is_active=True
        )
        vpv2 = VendorProductVariant.objects.create(
            vendor_product=vp, product_variant=self.variant2, price=1100.00, stock=10, is_active=True
        )

        self.client.force_authenticate(user=self.approved_user_a)
        detail_url = reverse("vendor-product-detail", kwargs={"pk": vp.id})

        # PATCH only sending variant 1
        patch_data = {
            "vendor_variants": [
                {
                    "product_variant": self.variant1.id,
                    "price": 950.00
                }
            ]
        }
        response = self.client.patch(detail_url, patch_data, format="json")
        self.assertEqual(response.status_code, 200)

        # Verify variant 2 is preserved and unchanged in database
        vpv2.refresh_from_db()
        self.assertEqual(vpv2.price, 1100.00)
        self.assertEqual(vpv2.stock, 10)
        self.assertTrue(vpv2.is_active)

    def test_explicit_variant_deletion(self):
        vp = VendorProduct.objects.create(
            vendor=self.vendor_a, product=self.product_with_var, status="DRAFT"
        )
        vpv1 = VendorProductVariant.objects.create(
            vendor_product=vp, product_variant=self.variant1, price=900.00, stock=5
        )

        self.client.force_authenticate(user=self.approved_user_a)
        delete_url = reverse("vendor-product-variant-delete", kwargs={"pk": vpv1.id})

        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(VendorProductVariant.objects.filter(id=vpv1.id).exists())

    def test_transaction_rollback_boundary(self):
        self.client.force_authenticate(user=self.approved_user_a)
        create_url = reverse("vendor-product-list")

        # Pass one valid variant and one invalid variant (belonging to other product)
        data = {
            "product": self.product_with_var.id,
            "status": "DRAFT",
            "vendor_variants": [
                {
                    "product_variant": self.variant1.id,
                    "price": 950.00,
                    "stock": 10
                },
                {
                    "product_variant": self.other_variant.id, # Invalid cross-product variant
                    "price": 5.00,
                    "stock": 1
                }
            ]
        }
        # Call API
        response = self.client.post(create_url, data, format="json")
        self.assertEqual(response.status_code, 400)

        # Check rollback: absolutely no VendorProduct or VendorProductVariant should be created
        self.assertFalse(VendorProduct.objects.filter(vendor=self.vendor_a, product=self.product_with_var).exists())


from unittest.mock import patch
from apps.vendors.models import VendorInventoryMovement

class VendorInventoryTests(APITestCase):
    def setUp(self):
        # Create users
        self.vendor_a_user = User.objects.create_user(username="vendor_a", email="a@example.com", password="pass", role="vendor")
        self.vendor_b_user = User.objects.create_user(username="vendor_b", email="b@example.com", password="pass", role="vendor")
        self.customer_user = User.objects.create_user(username="customer", email="c@example.com", password="pass", role="customer")
        self.suspended_vendor_user = User.objects.create_user(username="suspended", email="s@example.com", password="pass", role="vendor")

        # Profiles
        self.vendor_a = Vendor.objects.create(user=self.vendor_a_user, business_name="A LLC", store_name="A Store", business_email="a@store.com", status="APPROVED")
        self.vendor_b = Vendor.objects.create(user=self.vendor_b_user, business_name="B LLC", store_name="B Store", business_email="b@store.com", status="APPROVED")
        self.suspended_vendor = Vendor.objects.create(user=self.suspended_vendor_user, business_name="Susp LLC", store_name="Susp Store", business_email="s@store.com", status="SUSPENDED")

        # Products
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.simple_product = Product.objects.create(category=self.category, name="Simple Phone", slug="simple-phone")
        
        self.variant_product = Product.objects.create(category=self.category, name="Variant Phone", slug="variant-phone")
        self.variant_1 = ProductVariant.objects.create(product=self.variant_product, name="64GB", sku="64GB", price=100.0, stock=10)
        self.variant_2 = ProductVariant.objects.create(product=self.variant_product, name="128GB", sku="128GB", price=150.0, stock=20)

        # Listings Vendor A
        self.vp_simple_a = VendorProduct.objects.create(vendor=self.vendor_a, product=self.simple_product, status="ACTIVE", price=120.0, stock=15)
        self.vp_variant_a = VendorProduct.objects.create(vendor=self.vendor_a, product=self.variant_product, status="ACTIVE")
        self.vpv_1_a = VendorProductVariant.objects.create(vendor_product=self.vp_variant_a, product_variant=self.variant_1, price=95.0, stock=8)
        self.vpv_2_a = VendorProductVariant.objects.create(vendor_product=self.vp_variant_a, product_variant=self.variant_2, price=145.0, stock=12)

        # Listings Vendor B
        self.vp_simple_b = VendorProduct.objects.create(vendor=self.vendor_b, product=self.simple_product, status="ACTIVE", price=130.0, stock=5)

    def test_authorization_rules(self):
        # Guest gets 401
        url_list = reverse("vendor-inventory-list")
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 401)

        # Customer gets 403
        self.client.force_authenticate(user=self.customer_user)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 403)

        # Suspended vendor gets 403
        self.client.force_authenticate(user=self.suspended_vendor_user)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 403)

        # Approved vendor gets 200
        self.client.force_authenticate(user=self.vendor_a_user)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, 200)

    def test_ownership_protection(self):
        # Vendor A cannot see Vendor B simple product stock detail
        self.client.force_authenticate(user=self.vendor_a_user)
        url_detail = reverse("vendor-inventory-detail", kwargs={"pk": self.vp_simple_b.id})
        response = self.client.get(url_detail)
        self.assertEqual(response.status_code, 404)

        # Vendor A cannot post movement against Vendor B stock
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_b.id})
        payload = {"movement_type": "IN", "quantity": 10, "reason": "RESTOCK"}
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 404)

    def test_stock_in_success(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        payload = {"movement_type": "IN", "quantity": 10, "reason": "RESTOCK", "note": "Restocking phone"}
        
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 201)
        
        # Verify db fields
        self.vp_simple_a.refresh_from_db()
        self.assertEqual(self.vp_simple_a.stock, 25)
        
        movement = VendorInventoryMovement.objects.get(id=response.data["id"])
        self.assertEqual(movement.quantity, 10)
        self.assertEqual(movement.previous_stock, 15)
        self.assertEqual(movement.new_stock, 25)
        self.assertEqual(movement.reason, "RESTOCK")
        self.assertEqual(movement.note, "Restocking phone")
        self.assertEqual(movement.product_name_snapshot, "Simple Phone")

    def test_stock_out_success(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        payload = {"movement_type": "OUT", "quantity": 5, "reason": "LOST"}
        
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 201)
        
        self.vp_simple_a.refresh_from_db()
        self.assertEqual(self.vp_simple_a.stock, 10)
        
        movement = VendorInventoryMovement.objects.get(id=response.data["id"])
        self.assertEqual(movement.quantity, -5)
        self.assertEqual(movement.new_stock, 10)

    def test_negative_stock_protection(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        payload = {"movement_type": "OUT", "quantity": 20, "reason": "LOST"} # 20 > 15
        
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 400)
        
        # Verify db did not change and no movement log created
        self.vp_simple_a.refresh_from_db()
        self.assertEqual(self.vp_simple_a.stock, 15)
        self.assertFalse(VendorInventoryMovement.objects.filter(vendor_product=self.vp_simple_a).exists())

    def test_adjustment_signed_delta(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        
        # Positive adjustment
        payload = {"movement_type": "ADJUSTMENT", "quantity": 3, "reason": "STOCK_COUNT_CORRECTION"}
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.vp_simple_a.refresh_from_db()
        self.assertEqual(self.vp_simple_a.stock, 18)

        # Negative adjustment
        payload = {"movement_type": "ADJUSTMENT", "quantity": -5, "reason": "MANUAL_ADJUSTMENT"}
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.vp_simple_a.refresh_from_db()
        self.assertEqual(self.vp_simple_a.stock, 13)

    def test_sensible_reason_pairing_validation(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        
        # IN with OUT reason
        payload = {"movement_type": "IN", "quantity": 10, "reason": "LOST"}
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 400)

        # OUT with ADJUSTMENT reason
        payload = {"movement_type": "OUT", "quantity": 10, "reason": "STOCK_COUNT_CORRECTION"}
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_variant_stock_updates_correctly(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-variant-movement", kwargs={"product_id": self.vp_variant_a.id, "variant_id": self.vpv_1_a.id})
        payload = {"movement_type": "IN", "quantity": 5, "reason": "RESTOCK"}
        
        response = self.client.post(url_move, payload, format="json")
        self.assertEqual(response.status_code, 201)
        
        self.vpv_1_a.refresh_from_db()
        self.assertEqual(self.vpv_1_a.stock, 13)
        self.assertEqual(self.variant_1.stock, 10) # Master Catalog Variant stock remains unchanged!

    def test_movement_immutability(self):
        # Create a valid movement
        movement = VendorInventoryMovement.objects.create(
            vendor=self.vendor_a,
            vendor_product=self.vp_simple_a,
            movement_type="IN",
            quantity=10,
            previous_stock=0,
            new_stock=10,
            reason="INITIAL_STOCK",
            created_by=self.vendor_a_user
        )
        
        # Attempt update
        movement.quantity = 20
        with self.assertRaises(Exception):
            movement.save()

        # Attempt delete
        with self.assertRaises(Exception):
            movement.delete()

    def test_block_deletion_on_active_history(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        
        # Create inventory movement log
        VendorInventoryMovement.objects.create(
            vendor=self.vendor_a,
            vendor_product=self.vp_simple_a,
            movement_type="IN",
            quantity=10,
            previous_stock=0,
            new_stock=10,
            reason="INITIAL_STOCK",
            created_by=self.vendor_a_user
        )

        # Attempt listing delete through API -> Should fail
        url_delete = reverse("vendor-product-detail", kwargs={"pk": self.vp_simple_a.id})
        response = self.client.delete(url_delete)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(VendorProduct.objects.filter(id=self.vp_simple_a.id).exists())

    def test_url_ordering_no_conflict(self):
        self.client.force_authenticate(user=self.vendor_a_user)
        url_history = reverse("vendor-inventory-history")
        response = self.client.get(url_history)
        self.assertEqual(response.status_code, 200)

    @patch('django.db.models.query.QuerySet.select_for_update')
    def test_concurrency_locking_strategy(self, mock_select_for_update):
        # Return a queryset mimicking select_for_update
        mock_select_for_update.return_value = VendorProduct.objects.filter(id=self.vp_simple_a.id)
        self.client.force_authenticate(user=self.vendor_a_user)
        url_move = reverse("vendor-product-movement", kwargs={"product_id": self.vp_simple_a.id})
        payload = {"movement_type": "IN", "quantity": 10, "reason": "RESTOCK"}
        self.client.post(url_move, payload, format="json")
        mock_select_for_update.assert_called()


class VendorProductSerializerInventoryLedgerTests(APITestCase):
    def setUp(self):
        self.vendor_user = User.objects.create_user(username="lead_vendor", email="lead@example.com", password="password123", role="vendor")
        self.vendor = Vendor.objects.create(user=self.vendor_user, business_name="Lead Vendor LLC", store_name="Lead Store", business_email="lead@store.com", status="APPROVED")
        
        self.other_vendor_user = User.objects.create_user(username="other_vendor", email="other@example.com", password="password123", role="vendor")
        self.other_vendor = Vendor.objects.create(user=self.other_vendor_user, business_name="Other Vendor LLC", store_name="Other Store", business_email="other@store.com", status="APPROVED")

        self.category = Category.objects.create(name="Gadgets", slug="gadgets")
        self.variant_product = Product.objects.create(category=self.category, name="Smart Watch", slug="smart-watch")
        self.v1 = ProductVariant.objects.create(product=self.variant_product, name="Black", sku="SW-BLK", price=100.0, stock=50)
        self.v2 = ProductVariant.objects.create(product=self.variant_product, name="Silver", sku="SW-SLV", price=120.0, stock=50)

    def test_variant_creation_records_initial_stock_ledger(self):
        """
        Test 1: Variant creation with stock=20 records an INITIAL_STOCK ledger entry.
        """
        self.client.force_authenticate(user=self.vendor_user)
        url = reverse("vendor-product-list")
        payload = {
            "product": self.variant_product.id,
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "99.99",
                    "stock": 20,
                    "vendor_sku": "V-SW-BLK",
                    "is_active": True
                }
            ]
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        vp_id = response.data["id"]
        vpv = VendorProductVariant.objects.get(vendor_product_id=vp_id, product_variant=self.v1)
        self.assertEqual(vpv.stock, 20)

        movements = VendorInventoryMovement.objects.filter(vendor_product_variant=vpv)
        self.assertEqual(movements.count(), 1)
        m = movements.first()
        self.assertEqual(m.movement_type, "IN")
        self.assertEqual(m.reason, "INITIAL_STOCK")
        self.assertEqual(m.quantity, 20)
        self.assertEqual(m.previous_stock, 0)
        self.assertEqual(m.new_stock, 20)

    def test_variant_stock_increase_creates_adjustment(self):
        """
        Test 2: Updating variant stock from 10 to 50 creates an ADJUSTMENT ledger entry with quantity 40.
        """
        self.client.force_authenticate(user=self.vendor_user)
        vp = VendorProduct.objects.create(vendor=self.vendor, product=self.variant_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(vendor_product=vp, product_variant=self.v1, price=99.99, stock=10)

        url = reverse("vendor-product-detail", kwargs={"pk": vp.id})
        payload = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "99.99",
                    "stock": 50
                }
            ]
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        vpv.refresh_from_db()
        self.assertEqual(vpv.stock, 50)

        movements = VendorInventoryMovement.objects.filter(vendor_product_variant=vpv)
        self.assertEqual(movements.count(), 1)
        m = movements.first()
        self.assertEqual(m.movement_type, "ADJUSTMENT")
        self.assertEqual(m.reason, "STOCK_COUNT_CORRECTION")
        self.assertEqual(m.quantity, 40)
        self.assertEqual(m.previous_stock, 10)
        self.assertEqual(m.new_stock, 50)

    def test_variant_stock_decrease_creates_adjustment(self):
        """
        Test 3: Updating variant stock from 50 to 20 creates an ADJUSTMENT ledger entry with quantity -30.
        """
        self.client.force_authenticate(user=self.vendor_user)
        vp = VendorProduct.objects.create(vendor=self.vendor, product=self.variant_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(vendor_product=vp, product_variant=self.v1, price=99.99, stock=50)

        url = reverse("vendor-product-detail", kwargs={"pk": vp.id})
        payload = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "99.99",
                    "stock": 20
                }
            ]
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        vpv.refresh_from_db()
        self.assertEqual(vpv.stock, 20)

        movements = VendorInventoryMovement.objects.filter(vendor_product_variant=vpv)
        self.assertEqual(movements.count(), 1)
        m = movements.first()
        self.assertEqual(m.movement_type, "ADJUSTMENT")
        self.assertEqual(m.reason, "STOCK_COUNT_CORRECTION")
        self.assertEqual(m.quantity, -30)
        self.assertEqual(m.previous_stock, 50)
        self.assertEqual(m.new_stock, 20)

    def test_no_stock_change_creates_no_movement(self):
        """
        Test 4: Updating variant without changing stock (20 -> 20) creates zero movement entries.
        """
        self.client.force_authenticate(user=self.vendor_user)
        vp = VendorProduct.objects.create(vendor=self.vendor, product=self.variant_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(vendor_product=vp, product_variant=self.v1, price=99.99, stock=20)

        url = reverse("vendor-product-detail", kwargs={"pk": vp.id})
        payload = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "109.99",
                    "stock": 20
                }
            ]
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        vpv.refresh_from_db()
        self.assertEqual(vpv.stock, 20)
        self.assertEqual(VendorInventoryMovement.objects.filter(vendor_product_variant=vpv).count(), 0)

    def test_negative_stock_rejected(self):
        """
        Test 5: Attempting stock = -1 returns HTTP 400 Bad Request without mutating stock or creating movements.
        """
        self.client.force_authenticate(user=self.vendor_user)
        vp = VendorProduct.objects.create(vendor=self.vendor, product=self.variant_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(vendor_product=vp, product_variant=self.v1, price=99.99, stock=10)

        url = reverse("vendor-product-detail", kwargs={"pk": vp.id})
        payload = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "99.99",
                    "stock": -1
                }
            ]
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        vpv.refresh_from_db()
        self.assertEqual(vpv.stock, 10)
        self.assertEqual(VendorInventoryMovement.objects.filter(vendor_product_variant=vpv).count(), 0)

    def test_vendor_isolation_prevents_unauthorized_stock_mutation(self):
        """
        Test 6: Vendor B attempting to update Vendor A's variant fails (404 Not Found) with no stock mutation.
        """
        self.client.force_authenticate(user=self.other_vendor_user)
        vp = VendorProduct.objects.create(vendor=self.vendor, product=self.variant_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(vendor_product=vp, product_variant=self.v1, price=99.99, stock=10)

        url = reverse("vendor-product-detail", kwargs={"pk": vp.id})
        payload = {
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "product_variant": self.v1.id,
                    "price": "99.99",
                    "stock": 100
                }
            ]
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, 404)
        vpv.refresh_from_db()
        self.assertEqual(vpv.stock, 10)
        self.assertEqual(VendorInventoryMovement.objects.filter(vendor_product_variant=vpv).count(), 0)







from rest_framework.test import APITestCase

class VendorProductsManagementAPITests(APITestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant, VendorInventoryMovement
        from apps.products.models import Category, Product, ProductVariant

        User = get_user_model()
        self.user_a = User.objects.create_user(username="vendor_a", email="vendor_a@example.com", password="password123", role="vendor")
        self.vendor_a = Vendor.objects.create(user=self.user_a, business_name="Vendor A Store", store_name="Vendor A", business_email="vendor_a@example.com", business_phone="1111111111", status="APPROVED")

        self.user_b = User.objects.create_user(username="vendor_b", email="vendor_b@example.com", password="password123", role="vendor")
        self.vendor_b = Vendor.objects.create(user=self.user_b, business_name="Vendor B Store", store_name="Vendor B", business_email="vendor_b@example.com", business_phone="2222222222", status="APPROVED")

        self.category = Category.objects.create(name="Electronics", slug="electronics")
        
        # Simple product (no master variants)
        self.simple_product = Product.objects.create(category=self.category, name="Wireless Mouse", slug="wireless-mouse", description="Optical mouse")
        
        # Variant product (with master variants)
        self.variant_product = Product.objects.create(category=self.category, name="Mechanical Keyboard", slug="mech-keyboard", description="RGB Keyboard")
        self.var_red = ProductVariant.objects.create(product=self.variant_product, name="Red Switch", sku="KB-RED", price=50.0, stock=100)
        self.var_blue = ProductVariant.objects.create(product=self.variant_product, name="Blue Switch", sku="KB-BLUE", price=55.0, stock=100)

        # Existing listings
        self.vp_a = VendorProduct.objects.create(vendor=self.vendor_a, product=self.simple_product, price=25.00, stock=30, status="ACTIVE")
        self.vp_b = VendorProduct.objects.create(vendor=self.vendor_b, product=self.simple_product, price=29.00, stock=10, status="ACTIVE")

    def test_vendor_list_own_products_only(self):
        from django.urls import reverse
        self.client.force_authenticate(user=self.user_a)
        url = reverse("vendor-product-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        results = response.data if isinstance(response.data, list) else response.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.vp_a.id)

    def test_vendor_list_search_and_filter(self):
        from django.urls import reverse
        self.client.force_authenticate(user=self.user_a)
        url = reverse("vendor-product-list") + "?search=Wireless"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        results = response.data if isinstance(response.data, list) else response.data.get("results", [])
        self.assertEqual(len(results), 1)

        url_no_match = reverse("vendor-product-list") + "?search=NonExistentProduct"
        resp_no_match = self.client.get(url_no_match)
        self.assertEqual(resp_no_match.status_code, 200)
        results_no_match = resp_no_match.data if isinstance(resp_no_match.data, list) else resp_no_match.data.get("results", [])
        self.assertEqual(len(results_no_match), 0)

    def test_vendor_cannot_get_or_update_other_vendor_product(self):
        from django.urls import reverse
        self.client.force_authenticate(user=self.user_a)
        url = reverse("vendor-product-detail", kwargs={"pk": self.vp_b.id})
        
        # GET returns 404
        response_get = self.client.get(url)
        self.assertEqual(response_get.status_code, 404)

        # PATCH returns 404
        response_patch = self.client.patch(url, {"price": 10.00}, format="json")
        self.assertEqual(response_patch.status_code, 404)

    def test_simple_product_stock_adjustment_via_ledger(self):
        from django.urls import reverse
        from apps.vendors.models import VendorInventoryMovement
        self.client.force_authenticate(user=self.user_a)
        url = reverse("vendor-product-detail", kwargs={"pk": self.vp_a.id})
        
        # Increase stock from 30 to 45
        response = self.client.patch(url, {"stock": 45}, format="json")
        self.assertEqual(response.status_code, 200)
        self.vp_a.refresh_from_db()
        self.assertEqual(self.vp_a.stock, 45)

        movements = VendorInventoryMovement.objects.filter(vendor_product=self.vp_a)
        self.assertEqual(movements.count(), 1)
        m = movements.first()
        self.assertEqual(m.movement_type, "ADJUSTMENT")
        self.assertEqual(m.quantity, 15)
        self.assertEqual(m.previous_stock, 30)
        self.assertEqual(m.new_stock, 45)

    def test_cannot_delete_listing_with_inventory_movements(self):
        from django.urls import reverse
        from apps.vendors.models import VendorInventoryMovement
        # Record a movement for vp_a
        VendorInventoryMovement.objects.create(
            vendor=self.vendor_a,
            vendor_product=self.vp_a,
            product_name_snapshot="Wireless Mouse",
            movement_type="IN",
            quantity=30,
            previous_stock=0,
            new_stock=30,
            reason="INITIAL_STOCK",
            created_by=self.user_a
        )

        self.client.force_authenticate(user=self.user_a)
        url = reverse("vendor-product-detail", kwargs={"pk": self.vp_a.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 400)

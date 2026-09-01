from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status

from apps.accounts.models import Address
from apps.vendors.models import Vendor, VendorBankAccount, VendorProduct
from apps.products.models import Category, Product
from apps.orders.models import Order, OrderItem, VendorOrder
from apps.settlements.models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment
from apps.settlements.services import (
    generate_vendor_settlements,
    submit_settlement_for_approval,
    approve_settlement,
    process_settlement_payout,
    add_settlement_adjustment,
)

User = get_user_model()


class VendorSettlementTests(TestCase):
    def setUp(self):
        # 1. Users
        self.admin_user = User.objects.create_user(
            username="admin_user",
            email="admin@example.com",
            password="Password123!",
            role="admin"
        )
        self.vendor_user_1 = User.objects.create_user(
            username="vendor_user_1",
            email="vendor1@example.com",
            password="Password123!",
            role="vendor"
        )
        self.vendor_user_2 = User.objects.create_user(
            username="vendor_user_2",
            email="vendor2@example.com",
            password="Password123!",
            role="vendor"
        )
        self.customer_user = User.objects.create_user(
            username="customer_user",
            email="customer@example.com",
            password="Password123!",
            role="customer"
        )

        # 2. Vendors
        self.vendor_1 = Vendor.objects.create(
            user=self.vendor_user_1,
            business_name="Tech Store Inc",
            store_name="TechStore",
            business_email="tech@example.com",
            business_phone="1234567890",
            status="APPROVED",
            commission_rate=Decimal("10.00")
        )
        self.bank_1 = VendorBankAccount.objects.create(
            vendor=self.vendor_1,
            account_holder_name="Tech Store Inc",
            account_number="123456789012",
            bank_name="HDFC Bank",
            branch_name="Main Branch",
            ifsc_code="HDFC0001234"
        )

        self.vendor_2 = Vendor.objects.create(
            user=self.vendor_user_2,
            business_name="Fashion Hub",
            store_name="FashionHub",
            business_email="fashion@example.com",
            business_phone="0987654321",
            status="APPROVED",
            commission_rate=Decimal("15.00")
        )

        # 3. Product & Address
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(
            name="Smartphone",
            slug="smartphone",
            category=self.category,
            is_active=True
        )
        self.vendor_product_1 = VendorProduct.objects.create(
            vendor=self.vendor_1,
            product=self.product,
            status="ACTIVE",
            price=Decimal("1000.00"),
            stock=50
        )
        self.address = Address.objects.create(
            user=self.customer_user,
            full_name="John Doe",
            phone="9999999999",
            address_line_1="123 Street",
            city="Indore",
            state="MP",
            country="India",
            postal_code="452001"
        )

        # 4. Master Order & Vendor Sub-Order 1 (Delivered & Paid)
        self.master_order_1 = Order.objects.create(
            user=self.customer_user,
            address=self.address,
            snapshot_full_name="John Doe",
            snapshot_phone="9999999999",
            snapshot_address_line_1="123 Street",
            snapshot_city="Indore",
            snapshot_state="MP",
            snapshot_postal_code="452001",
            status=Order.DELIVERED,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("1000.00"),
            discount=Decimal("100.00"),
            total_amount=Decimal("900.00")
        )
        self.vendor_order_1 = VendorOrder.objects.create(
            master_order=self.master_order_1,
            vendor=self.vendor_1,
            status=VendorOrder.DELIVERED,
            subtotal=Decimal("1000.00"),
            discount=Decimal("100.00"),
            total_amount=Decimal("900.00")
        )
        self.order_item_1 = OrderItem.objects.create(
            order=self.master_order_1,
            vendor_order=self.vendor_order_1,
            product=self.product,
            product_name="Smartphone",
            price=Decimal("1000.00"),
            quantity=1,
            total=Decimal("1000.00")
        )

        # API Clients
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin_user)

        self.vendor_client_1 = APIClient()
        self.vendor_client_1.force_authenticate(user=self.vendor_user_1)

        self.vendor_client_2 = APIClient()
        self.vendor_client_2.force_authenticate(user=self.vendor_user_2)

    def test_settlement_generation_and_math_precision(self):
        """
        Verify settlement generation calculates gross sales, allocated discount,
        commission (10% of commissionable base), and net payable using exact Decimal precision.
        """
        today = date.today()
        period_start = today - timedelta(days=7)
        period_end = today + timedelta(days=1)

        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=period_start,
            period_end=period_end,
            user=self.admin_user
        )

        self.assertEqual(settlement.status, VendorSettlement.DRAFT)
        self.assertEqual(settlement.items.count(), 1)
        item = settlement.items.first()

        # Math verification:
        # Gross = 1000.00, Discount = 100.00 -> Base = 900.00
        # Commission Rate = 10.00% -> Commission Amount = 90.00
        # Net Payable = 900.00 - 90.00 = 810.00
        self.assertEqual(item.gross_amount, Decimal("1000.00"))
        self.assertEqual(item.discount_amount, Decimal("100.00"))
        self.assertEqual(item.commission_amount, Decimal("90.00"))
        self.assertEqual(item.net_payable, Decimal("810.00"))

        self.assertEqual(settlement.gross_amount, Decimal("1000.00"))
        self.assertEqual(settlement.discount_amount, Decimal("100.00"))
        self.assertEqual(settlement.commission_amount, Decimal("90.00"))
        self.assertEqual(settlement.net_payable, Decimal("810.00"))

    def test_settlement_generation_idempotency(self):
        """
        Verify that generating settlement twice for the same period rejects duplicate line items.
        """
        today = date.today()
        period_start = today - timedelta(days=7)
        period_end = today + timedelta(days=1)

        generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=period_start,
            period_end=period_end,
            user=self.admin_user
        )

        # Second run should raise ValidationError because vendor_order_1 is already attached to a settlement item
        with self.assertRaises(ValidationError):
            generate_vendor_settlements(
                vendor=self.vendor_1,
                period_start=period_start,
                period_end=period_end,
                user=self.admin_user
            )

    def test_commission_rate_snapshot_preservation(self):
        """
        Assert that changing vendor.commission_rate later does NOT alter existing settlement items.
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        # Alter live vendor commission rate
        self.vendor_1.commission_rate = Decimal("25.00")
        self.vendor_1.save()

        item = settlement.items.first()
        self.assertEqual(item.vendor_commission_rate_snapshot, Decimal("10.00"))
        self.assertEqual(item.commission_amount, Decimal("90.00"))

    def test_cancelled_orders_are_not_eligible(self):
        """
        Verify that CANCELLED vendor orders are excluded from settlement generation.
        """
        cancelled_master = Order.objects.create(
            user=self.customer_user,
            address=self.address,
            status=Order.CANCELLED,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("500.00"),
            total_amount=Decimal("500.00")
        )
        VendorOrder.objects.create(
            master_order=cancelled_master,
            vendor=self.vendor_2,
            status=VendorOrder.CANCELLED,
            subtotal=Decimal("500.00"),
            total_amount=Decimal("500.00")
        )

        today = date.today()
        with self.assertRaises(ValidationError):
            generate_vendor_settlements(
                vendor=self.vendor_2,
                period_start=today - timedelta(days=7),
                period_end=today + timedelta(days=1),
                user=self.admin_user
            )

    def test_immutability_from_pending_approval(self):
        """
        Assert that financial fields and line items cannot be modified once settlement reaches PENDING_APPROVAL.
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        submit_settlement_for_approval(settlement, self.admin_user)
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, VendorSettlement.PENDING_APPROVAL)

        # 1. Direct mutation of financial totals fails
        settlement.gross_amount = Decimal("9999.00")
        with self.assertRaises(ValidationError):
            settlement.save()

        # 2. Adding adjustments after PENDING_APPROVAL fails
        with self.assertRaises(ValidationError):
            add_settlement_adjustment(
                settlement=settlement,
                adjustment_type="DEBIT",
                amount=Decimal("50.00"),
                reason="Penalty",
                user=self.admin_user
            )

    def test_approval_and_bank_snapshot(self):
        """
        Verify that approving a settlement snapshots vendor bank details with masked account number.
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        submit_settlement_for_approval(settlement, self.admin_user)
        approve_settlement(settlement, self.admin_user)
        settlement.refresh_from_db()

        self.assertEqual(settlement.status, VendorSettlement.APPROVED)
        self.assertEqual(settlement.bank_account_holder_snapshot, "Tech Store Inc")
        self.assertEqual(settlement.bank_name_snapshot, "HDFC Bank")
        self.assertEqual(settlement.bank_account_number_masked_snapshot, "XXXX-XXXX-9012")
        self.assertEqual(settlement.ifsc_code_snapshot, "HDFC0001234")

    def test_payout_processing_and_unrevertible_settled_state(self):
        """
        Verify payout processing marks settlement SETTLED & payout_status PAID,
        and prevents reverting settled payout status.
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        submit_settlement_for_approval(settlement, self.admin_user)
        approve_settlement(settlement, self.admin_user)
        process_settlement_payout(settlement, payout_reference="UTR12345678", payout_method="NEFT", user=self.admin_user)

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, VendorSettlement.SETTLED)
        self.assertEqual(settlement.payout_status, VendorSettlement.PAYOUT_PAID)
        self.assertEqual(settlement.payout_reference, "UTR12345678")

        # Attempting to revert status fails
        settlement.status = VendorSettlement.DRAFT
        with self.assertRaises(ValidationError):
            settlement.save()

    def test_negative_net_payable_rejection(self):
        """
        Verify that an adjustment resulting in negative net_payable is rejected.
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        # Net payable is 810.00. Add DEBIT adjustment of 900.00 (resulting in -90.00 net payable)
        with self.assertRaises(ValidationError):
            add_settlement_adjustment(
                settlement=settlement,
                adjustment_type="DEBIT",
                amount=Decimal("900.00"),
                reason="Excessive Penalty",
                user=self.admin_user
            )

    def test_vendor_ownership_isolation(self):
        """
        Assert Vendor 2 cannot view Vendor 1's settlement endpoints (HTTP 404).
        """
        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        # Vendor 1 can access detail view
        res1 = self.vendor_client_1.get(f"/api/vendors/settlements/{settlement.id}/")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        # Vendor 2 gets 404 Not Found
        res2 = self.vendor_client_2.get(f"/api/vendors/settlements/{settlement.id}/")
        self.assertEqual(res2.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_api_workflow(self):
        """
        Test complete administrative API flow: generate -> submit -> approve -> process-payout.
        """
        today = date.today()
        p_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        p_end = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        # 1. Admin Generate API
        gen_res = self.admin_client.post("/api/admin/settlements/generate/", {
            "vendor_id": self.vendor_1.id,
            "period_start": p_start,
            "period_end": p_end
        }, format="json")
        self.assertEqual(gen_res.status_code, status.HTTP_201_CREATED)
        settlement_id = gen_res.data["id"]

        # 2. Admin Submit API
        sub_res = self.admin_client.post(f"/api/admin/settlements/{settlement_id}/submit/")
        self.assertEqual(sub_res.status_code, status.HTTP_200_OK)
        self.assertEqual(sub_res.data["status"], "PENDING_APPROVAL")

        # 3. Admin Approve API
        app_res = self.admin_client.post(f"/api/admin/settlements/{settlement_id}/approve/")
        self.assertEqual(app_res.status_code, status.HTTP_200_OK)
        self.assertEqual(app_res.data["status"], "APPROVED")

        # 4. Admin Process Payout API
        pay_res = self.admin_client.post(f"/api/admin/settlements/{settlement_id}/process-payout/", {
            "payout_reference": "NEFT-TXN-998877",
            "payout_method": "NEFT"
        }, format="json")
        self.assertEqual(pay_res.status_code, status.HTTP_200_OK)
        self.assertEqual(pay_res.data["status"], "SETTLED")
        self.assertEqual(pay_res.data["payout_status"], "PAID")

    def test_generate_settlement_includes_unapplied_refund_adjustment(self):
        from apps.settlements.models import UnappliedRefundAdjustment
        from apps.returns.models import ReturnRequest

        return_req = ReturnRequest.objects.create(
            customer=self.customer_user,
            vendor=self.vendor_1,
            order=self.master_order_1,
            vendor_order=self.vendor_order_1,
            order_item=self.order_item_1,
            quantity=1,
            reason=ReturnRequest.DEFECTIVE,
            status=ReturnRequest.REFUNDED
        )

        unapplied = UnappliedRefundAdjustment.objects.create(
            vendor=self.vendor_1,
            return_request=return_req,
            vendor_order=self.vendor_order_1,
            amount=Decimal("100.00"),
            adjustment_type="DEBIT",
            reason="Deferred return DEBIT",
            is_applied=False
        )

        today = date.today()
        settlement = generate_vendor_settlements(
            vendor=self.vendor_1,
            period_start=today - timedelta(days=7),
            period_end=today + timedelta(days=1),
            user=self.admin_user
        )

        unapplied.refresh_from_db()
        self.assertTrue(unapplied.is_applied)
        self.assertEqual(unapplied.applied_settlement, settlement)
        self.assertEqual(settlement.adjustments.count(), 1)
        self.assertEqual(settlement.adjustments.first().amount, Decimal("100.00"))


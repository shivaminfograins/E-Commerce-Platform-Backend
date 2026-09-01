from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from django.core.exceptions import ValidationError

from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant, VendorInventoryMovement
from apps.products.models import Category, Product, ProductVariant
from apps.orders.models import Order, VendorOrder, OrderItem
from apps.payments.models import Transaction
from apps.settlements.models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment
from apps.returns.models import ReturnRequest, Refund
from apps.returns.services import (
    calculate_return_refund_amount,
    create_return_request,
    approve_return_request,
    reject_return_request,
    update_logistics_status,
    inspect_return_item,
    process_return_refund,
)

User = get_user_model()


class Module9ReturnsTestCase(TestCase):
    def setUp(self):
        # 1. Users
        self.customer = User.objects.create_user(username="customer1", email="customer1@example.com", password="password123")
        self.customer2 = User.objects.create_user(username="customer2", email="customer2@example.com", password="password123")
        
        self.vendor_user = User.objects.create_user(username="vendor1", email="vendor1@example.com", password="password123")
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Vendor Store One",
            business_name="Vendor Business One",
            business_email="vendor1@example.com",
            business_phone="1111111111",
            status="APPROVED",
            commission_rate=Decimal("10.00")
        )

        self.vendor_user2 = User.objects.create_user(username="vendor2", email="vendor2@example.com", password="password123")
        self.vendor2 = Vendor.objects.create(
            user=self.vendor_user2,
            store_name="Vendor Store Two",
            business_name="Vendor Business Two",
            business_email="vendor2@example.com",
            business_phone="2222222222",
            status="APPROVED",
            commission_rate=Decimal("10.00")
        )

        self.admin_user = User.objects.create_user(username="adminuser", email="admin@example.com", password="password123", is_staff=True, is_superuser=True)

        # 2. Product & Listing
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(name="Wireless Earbuds", category=self.category)
        self.variant = ProductVariant.objects.create(product=self.product, name="Black Edition", sku="EARBUD-BLK", price=Decimal("1000.00"))

        self.vendor_product = VendorProduct.objects.create(
            vendor=self.vendor,
            product=self.product,
            price=Decimal("1000.00"),
            stock=50,
            status="APPROVED"
        )
        self.vendor_product_variant = VendorProductVariant.objects.create(
            vendor_product=self.vendor_product,
            product_variant=self.variant,
            vendor_sku="EARBUD-BLK",
            price=Decimal("1000.00"),
            stock=50
        )

        # 3. Master Order & Sub-Order
        self.master_order = Order.objects.create(
            user=self.customer,
            order_number="ORD-TEST-9001",
            status=Order.DELIVERED,
            payment_status=Order.PAYMENT_PAID,
            payment_method="razorpay",
            subtotal=Decimal("2000.00"),
            discount=Decimal("200.00"),
            total_amount=Decimal("1800.00")
        )

        self.vendor_order = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor,
            vendor_order_number="VO-TEST-9001",
            status=VendorOrder.DELIVERED,
            subtotal=Decimal("2000.00"),
            discount=Decimal("200.00"),
            vendor_store_name_snapshot=self.vendor.store_name,
            vendor_business_name_snapshot=self.vendor.business_name
        )

        # OrderItem: 2 units @ ₹1000 = ₹2000 gross total
        self.order_item = OrderItem.objects.create(
            order=self.master_order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=Decimal("1000.00"),
            quantity=2,
            total=Decimal("2000.00")
        )

        self.payment_transaction = Transaction.objects.create(
            order=self.master_order,
            payment_method="razorpay",
            status=Transaction.SUCCESS,
            amount=Decimal("1800.00"),
            razorpay_order_id="order_mock_9001",
            transaction_id="pay_mock_9001"
        )

        self.client = APIClient()

    def test_financial_refund_calculation_anti_double_discounting(self):
        """
        Tests anti-double-discounting refund formula precision:
        Gross Item Total = ₹2,000. Subtotal = ₹2,000. Sub-order discount = ₹200.
        Net line total = ₹1,800. Net unit price = ₹900.
        Returning 1 unit yields exact ₹900.00 refund.
        """
        refund_amount = calculate_return_refund_amount(self.order_item, return_quantity=1)
        self.assertEqual(refund_amount, Decimal("900.00"))

    def test_partial_return_request_success(self):
        """
        Tests partial return request creation for 1 unit out of 2.
        """
        return_req = create_return_request(
            customer=self.customer,
            order_item_id=self.order_item.id,
            quantity=1,
            reason=ReturnRequest.DEFECTIVE,
            customer_note="Left earbud not charging"
        )
        self.assertEqual(return_req.quantity, 1)
        self.assertEqual(return_req.status, ReturnRequest.REQUESTED)
        self.assertTrue(return_req.return_number.startswith("RET-"))

    def test_multiple_partial_returns_against_same_order_item(self):
        """
        Tests that multiple partial returns against the same OrderItem are supported.
        First return 1 unit, then return 1 unit.
        """
        req1 = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        req2 = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.WRONG_SIZE)

        self.assertEqual(req1.quantity, 1)
        self.assertEqual(req2.quantity, 1)
        self.assertEqual(ReturnRequest.objects.filter(order_item=self.order_item).count(), 2)

    def test_return_quantity_exceeding_purchased_quantity_rejected(self):
        """
        Asserts that requesting a return quantity greater than purchased/remaining quantity is rejected.
        """
        # Try requesting 3 when only 2 purchased
        with self.assertRaises(ValidationError):
            create_return_request(self.customer, self.order_item.id, 3, ReturnRequest.DEFECTIVE)

        # Create return for 1
        create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)

        # Try requesting 2 when only 1 remaining
        with self.assertRaises(ValidationError):
            create_return_request(self.customer, self.order_item.id, 2, ReturnRequest.DEFECTIVE)

    def test_return_window_expiry_rejected(self):
        """
        Asserts that requesting a return after window expiry raises ValidationError.
        """
        # Backdate delivery to 10 days ago (window is 7 days) via .update() to bypass auto_now
        VendorOrder.objects.filter(id=self.vendor_order.id).update(updated_at=timezone.now() - timedelta(days=10))
        self.vendor_order.refresh_from_db()

        with self.assertRaises(ValidationError):
            create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)

    def test_invalid_state_transition_rejected(self):
        """
        Asserts that invalid state transitions raise ValidationError.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        
        # Try skipping approval to jump directly to IN_TRANSIT
        with self.assertRaises(ValidationError):
            update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)

    def test_sellable_return_restores_inventory_idempotently(self):
        """
        Asserts that an inspected SELLABLE return creates exactly ONE IN movement via Module 6,
        and re-calling inspection skips duplicate stock creation.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.CUSTOMER_CHANGED_MIND)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)

        initial_movements = VendorInventoryMovement.objects.filter(reference_type="RETURN", reference_id=return_req.return_number).count()
        self.assertEqual(initial_movements, 0)

        # First inspection as SELLABLE
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Item unopened in original packaging", self.vendor_user)
        
        movements_after = VendorInventoryMovement.objects.filter(reference_type="RETURN", reference_id=return_req.return_number)
        self.assertEqual(movements_after.count(), 1)
        self.assertEqual(movements_after.first().movement_type, "IN")
        self.assertEqual(movements_after.first().quantity, 1)

        # Attempting second inspection on already inspected item should be blocked
        with self.assertRaises(ValidationError):
            inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Duplicate call", self.vendor_user)

        # Confirm movements count remains exactly 1
        self.assertEqual(VendorInventoryMovement.objects.filter(reference_type="RETURN", reference_id=return_req.return_number).count(), 1)

    def test_damaged_return_does_not_restore_inventory(self):
        """
        Asserts that an inspected DAMAGED return does NOT restore sellable inventory stock.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DAMAGED)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)

        inspect_return_item(return_req.id, ReturnRequest.DAMAGED_COND, "Item physically cracked", self.vendor_user)
        
        movement_count = VendorInventoryMovement.objects.filter(reference_type="RETURN", reference_id=return_req.return_number).count()
        self.assertEqual(movement_count, 0)

    def test_prepaid_gateway_refund_workflow(self):
        """
        Tests end-to-end prepaid gateway refund processing.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Good condition", self.vendor_user)

        refund = process_return_refund(return_req.id, user=self.admin_user)
        self.assertEqual(refund.status, Refund.SUCCESS)
        self.assertEqual(refund.amount, Decimal("900.00"))
        self.assertTrue(refund.gateway_refund_id.startswith("rfnd_mock_"))

        return_req.refresh_from_db()
        self.assertEqual(return_req.status, ReturnRequest.REFUNDED)

    def test_cod_refund_requires_payout_reference(self):
        """
        Asserts that COD refunds require a manual bank payout reference (UTR) before marking status SUCCESS.
        """
        # Change order to COD
        self.master_order.payment_method = Order.COD
        self.master_order.save()

        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Good condition", self.vendor_user)

        # Processing COD refund without payout_reference raises ValidationError
        with self.assertRaises(ValidationError):
            process_return_refund(return_req.id, payout_reference="", user=self.admin_user)

        # Processing COD refund with payout_reference succeeds
        refund = process_return_refund(return_req.id, payout_reference="UTR987654321", user=self.admin_user)
        self.assertEqual(refund.status, Refund.SUCCESS)
        self.assertEqual(refund.payout_reference, "UTR987654321")

    def test_duplicate_refund_processing_prevented(self):
        """
        Asserts that re-processing an already successful refund returns the existing Refund object idempotently.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Good condition", self.vendor_user)

        refund1 = process_return_refund(return_req.id, user=self.admin_user)
        refund2 = process_return_refund(return_req.id, user=self.admin_user)

        self.assertEqual(refund1.id, refund2.id)
        self.assertEqual(Refund.objects.filter(return_request=return_req).count(), 1)

    def test_settled_vendor_order_creates_debit_settlement_adjustment(self):
        """
        Asserts that processing a refund for an ALREADY SETTLED VendorOrder attaches a DEBIT adjustment
        to an active DRAFT settlement statement for that vendor.
        """
        # 1. Create historical settlement in DRAFT status, attach item, then transition to SETTLED
        settlement = VendorSettlement.objects.create(
            vendor=self.vendor,
            status=VendorSettlement.DRAFT,
            payout_status=VendorSettlement.PAYOUT_UNPAID,
            gross_amount=Decimal("2000.00"),
            discount_amount=Decimal("200.00"),
            tax_amount=Decimal("0.00"),
            commission_amount=Decimal("180.00"),
            net_payable=Decimal("1620.00"),
            period_start=timezone.now().date() - timedelta(days=7),
            period_end=timezone.now().date()
        )
        VendorSettlementItem.objects.create(
            settlement=settlement,
            vendor_order=self.vendor_order,
            vendor_order_number_snapshot=self.vendor_order.vendor_order_number,
            order_status_snapshot=self.vendor_order.status,
            gross_amount=Decimal("2000.00"),
            discount_amount=Decimal("200.00"),
            vendor_commission_rate_snapshot=Decimal("10.00"),
            commission_amount=Decimal("180.00"),
            net_payable=Decimal("1620.00")
        )
        settlement.status = VendorSettlement.PENDING_APPROVAL
        settlement.save()
        settlement.status = VendorSettlement.APPROVED
        settlement.save()
        settlement.status = VendorSettlement.SETTLED
        settlement.payout_status = VendorSettlement.PAYOUT_PAID
        settlement.save()

        # 2. Create an active DRAFT settlement for the vendor with items
        draft_settlement = VendorSettlement.objects.create(
            vendor=self.vendor,
            status=VendorSettlement.DRAFT,
            payout_status=VendorSettlement.PAYOUT_UNPAID,
            gross_amount=Decimal("2000.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            commission_amount=Decimal("200.00"),
            net_payable=Decimal("1800.00"),
            period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=7)
        )
        # Create a second vendor order for draft settlement item
        vo2 = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor,
            vendor_order_number="VO-TEST-999",
            status=VendorOrder.DELIVERED,
            subtotal=Decimal("2000.00"),
            discount=Decimal("0.00"),
            tax=Decimal("0.00"),
            shipping_charge=Decimal("0.00"),
            total_amount=Decimal("2000.00")
        )
        VendorSettlementItem.objects.create(
            settlement=draft_settlement,
            vendor_order=vo2,
            vendor_order_number_snapshot=vo2.vendor_order_number,
            order_status_snapshot=vo2.status,
            gross_amount=Decimal("2000.00"),
            discount_amount=Decimal("0.00"),
            vendor_commission_rate_snapshot=Decimal("10.00"),
            commission_amount=Decimal("200.00"),
            net_payable=Decimal("1800.00")
        )

        # 3. Process return refund
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Good condition", self.vendor_user)
        process_return_refund(return_req.id, user=self.admin_user)

        # Assert DEBIT adjustment attached to draft_settlement
        adjustments = VendorSettlementAdjustment.objects.filter(settlement=draft_settlement)
        self.assertEqual(adjustments.count(), 1)
        self.assertEqual(adjustments.first().adjustment_type, "DEBIT")
        self.assertEqual(adjustments.first().amount, Decimal("900.00"))

    def test_vendor_isolation_unowned_returns_404(self):
        """
        Asserts that Vendor 2 receives HTTP 404 when attempting to view or update Vendor 1's return request.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        
        self.client.force_authenticate(user=self.vendor_user2)
        response = self.client.get(f"/api/vendors/returns/{return_req.return_number}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_customer_isolation_unowned_returns(self):
        """
        Asserts that Customer 2 receives HTTP 404 when attempting to query Customer 1's return request.
        """
        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        
        self.client.force_authenticate(user=self.customer2)
        response = self.client.get(f"/api/returns/{return_req.return_number}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_api_full_workflow(self):
        """
        Tests Admin API endpoints for approve, logistics update, inspection, and refund processing.
        """
        # Customer creates return
        self.client.force_authenticate(user=self.customer)
        res = self.client.post("/api/returns/", {
            "order_item_id": self.order_item.id,
            "quantity": 1,
            "reason": "DEFECTIVE",
            "customer_note": "Defective unit"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        ret_num = res.data["return_number"]
        ret_id = res.data["id"]

        # Admin approves
        self.client.force_authenticate(user=self.admin_user)
        res_app = self.client.post(f"/api/admin/returns/{ret_id}/approve/")
        self.assertEqual(res_app.status_code, status.HTTP_200_OK)
        self.assertEqual(res_app.data["status"], "APPROVED")

        # Vendor updates status to RECEIVED
        self.client.force_authenticate(user=self.vendor_user)
        self.client.patch(f"/api/vendors/returns/{ret_num}/status/", {"status": "PICKUP_PENDING"})
        self.client.patch(f"/api/vendors/returns/{ret_num}/status/", {"status": "IN_TRANSIT"})
        res_rec = self.client.patch(f"/api/vendors/returns/{ret_num}/status/", {"status": "RECEIVED"})
        self.assertEqual(res_rec.status_code, status.HTTP_200_OK)

        # Vendor inspects as SELLABLE
        res_insp = self.client.post(f"/api/vendors/returns/{ret_num}/inspect/", {
            "condition": "SELLABLE",
            "note": "Inspected OK"
        })
        self.assertEqual(res_insp.status_code, status.HTTP_200_OK)
        self.assertTrue(res_insp.data["inventory_restored"])

        # Admin refunds
        self.client.force_authenticate(user=self.admin_user)
        res_ref = self.client.post(f"/api/admin/returns/{ret_id}/refund/", {})
        self.assertEqual(res_ref.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ref.data["status"], "SUCCESS")

    def test_refund_after_settlement_creates_unapplied_refund_adjustment(self):
        from apps.settlements.models import VendorSettlement, VendorSettlementItem, UnappliedRefundAdjustment
        settlement = VendorSettlement.objects.create(
            vendor=self.vendor,
            settlement_number="SETTL-TEST-001",
            period_start=timezone.now().date() - timedelta(days=7),
            period_end=timezone.now().date(),
            status=VendorSettlement.DRAFT,
            payout_status=VendorSettlement.PAYOUT_UNPAID
        )
        VendorSettlementItem.objects.create(
            settlement=settlement,
            vendor_order=self.vendor_order,
            vendor_order_number_snapshot=self.vendor_order.vendor_order_number,
            order_status_snapshot=self.vendor_order.status,
            vendor_commission_rate_snapshot=Decimal("10.00"),
            gross_amount=self.vendor_order.subtotal,
            discount_amount=Decimal("0.00"),
            commission_amount=Decimal("100.00"),
            net_payable=Decimal("900.00")
        )
        VendorSettlement.objects.filter(pk=settlement.pk).update(
            status=VendorSettlement.SETTLED,
            payout_status=VendorSettlement.PAYOUT_PAID
        )

        return_req = create_return_request(self.customer, self.order_item.id, 1, ReturnRequest.DEFECTIVE)
        approve_return_request(return_req.id, self.admin_user)
        update_logistics_status(return_req.id, ReturnRequest.PICKUP_PENDING, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.IN_TRANSIT, self.vendor_user)
        update_logistics_status(return_req.id, ReturnRequest.RECEIVED, self.vendor_user)
        inspect_return_item(return_req.id, ReturnRequest.SELLABLE, "Inspected OK", self.vendor_user)

        refund = process_return_refund(return_req.id, self.admin_user)
        self.assertEqual(refund.status, Refund.SUCCESS)

        # Verify UnappliedRefundAdjustment created
        unapplied = UnappliedRefundAdjustment.objects.filter(return_request=return_req, vendor=self.vendor).first()
        self.assertIsNotNone(unapplied)
        self.assertFalse(unapplied.is_applied)
        self.assertEqual(unapplied.amount, Decimal("900.00"))


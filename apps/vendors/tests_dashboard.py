from decimal import Decimal
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model

from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant, VendorInventoryMovement
from apps.products.models import Product, Category, Brand, ProductVariant
from apps.orders.models import Order, VendorOrder, OrderItem
from apps.settlements.models import VendorSettlement, VendorSettlementItem, VendorSettlementAdjustment

User = get_user_model()


class VendorDashboardTests(APITestCase):
    def setUp(self):
        # Create Category & Brand
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.brand = Brand.objects.create(name="TechBrand", slug="techbrand")

        # Create Master Product
        self.master_product_1 = Product.objects.create(
            name="Smartphone X",
            slug="smartphone-x",
            description="Smart phone description",
            category=self.category,
            brand=self.brand,
            is_active=True
        )

        self.master_product_2 = Product.objects.create(
            name="Laptop Pro",
            slug="laptop-pro",
            description="Laptop description",
            category=self.category,
            brand=self.brand,
            is_active=True
        )

        # Create User A & Vendor A
        self.user_a = User.objects.create_user(
            username="vendor_a",
            email="vendor_a@example.com",
            password="password123",
            role="vendor"
        )
        self.vendor_a = Vendor.objects.create(
            user=self.user_a,
            business_name="Vendor A Ltd",
            store_name="Store A",
            business_email="vendor_a@example.com",
            business_phone="1234567890",
            status="APPROVED",
            commission_rate=Decimal("10.00")
        )

        # Create User B & Vendor B
        self.user_b = User.objects.create_user(
            username="vendor_b",
            email="vendor_b@example.com",
            password="password123",
            role="vendor"
        )
        self.vendor_b = Vendor.objects.create(
            user=self.user_b,
            business_name="Vendor B Ltd",
            store_name="Store B",
            business_email="vendor_b@example.com",
            business_phone="0987654321",
            status="APPROVED",
            commission_rate=Decimal("15.00")
        )

        # Create Customer User
        self.customer = User.objects.create_user(
            username="customer_user",
            email="customer@example.com",
            password="password123",
            role="customer"
        )

        self.url = reverse("vendor-dashboard")

    def test_unauthenticated_access_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_non_vendor_access_denied(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_approved_vendor_dashboard(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        self.assertEqual(data["store"]["name"], "Store A")
        self.assertEqual(data["store"]["status"], "APPROVED")

        # KPIs should be 0
        self.assertEqual(data["kpis"]["products"], 0)
        self.assertEqual(data["kpis"]["orders"], 0)
        self.assertEqual(data["kpis"]["action_required"], 0)
        self.assertEqual(data["kpis"]["net_earnings"], "0.00")

        # Orders breakdown
        self.assertEqual(data["orders"]["pending"], 0)
        self.assertEqual(data["orders"]["confirmed"], 0)
        self.assertEqual(data["orders"]["processing"], 0)
        self.assertEqual(data["orders"]["packed"], 0)
        self.assertEqual(data["orders"]["shipped"], 0)
        self.assertEqual(data["orders"]["delivered"], 0)

        # Inventory breakdown
        self.assertEqual(data["inventory"]["total_skus"], 0)
        self.assertEqual(data["inventory"]["in_stock"], 0)
        self.assertEqual(data["inventory"]["low_stock"], 0)
        self.assertEqual(data["inventory"]["out_of_stock"], 0)

        # Action orders & settlements
        self.assertEqual(data["action_orders"], [])
        self.assertEqual(data["settlement"]["total_sales"], "0.00")
        self.assertEqual(data["settlement"]["commission"], "0.00")
        self.assertEqual(data["settlement"]["settled"], "0.00")
        self.assertEqual(data["settlement"]["pending_payout"], "0.00")

    def test_vendor_tenant_isolation(self):
        # Create Vendor A product & order
        vp_a = VendorProduct.objects.create(
            vendor=self.vendor_a,
            product=self.master_product_1,
            price=Decimal("1000.00"),
            stock=15,
            status="ACTIVE"
        )
        master_order_a = Order.objects.create(
            user=self.customer,
            snapshot_full_name="John Doe",
            snapshot_phone="1234567890",
            snapshot_address_line_1="Line 1",
            snapshot_city="City",
            snapshot_state="State",
            snapshot_postal_code="123456",
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00")
        )
        vo_a = VendorOrder.objects.create(
            master_order=master_order_a,
            vendor=self.vendor_a,
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00")
        )

        # Create Vendor B product & order
        vp_b = VendorProduct.objects.create(
            vendor=self.vendor_b,
            product=self.master_product_2,
            price=Decimal("5000.00"),
            stock=30,
            status="ACTIVE"
        )
        master_order_b = Order.objects.create(
            user=self.customer,
            snapshot_full_name="Jane Doe",
            snapshot_phone="0987654321",
            snapshot_address_line_1="Line 2",
            snapshot_city="City 2",
            snapshot_state="State 2",
            snapshot_postal_code="654321",
            subtotal=Decimal("5000.00"),
            total_amount=Decimal("5000.00")
        )
        vo_b = VendorOrder.objects.create(
            master_order=master_order_b,
            vendor=self.vendor_b,
            status=VendorOrder.DELIVERED,
            subtotal=Decimal("5000.00"),
            total_amount=Decimal("5000.00")
        )

        # Verify Vendor A dashboard
        self.client.force_authenticate(user=self.user_a)
        res_a = self.client.get(self.url)
        self.assertEqual(res_a.data["kpis"]["products"], 1)
        self.assertEqual(res_a.data["kpis"]["orders"], 1)
        self.assertEqual(res_a.data["orders"]["confirmed"], 1)
        self.assertEqual(res_a.data["orders"]["delivered"], 0)
        self.assertEqual(len(res_a.data["action_orders"]), 1)
        self.assertEqual(res_a.data["action_orders"][0]["vendor_order_number"], vo_a.vendor_order_number)

        # Verify Vendor B dashboard
        self.client.force_authenticate(user=self.user_b)
        res_b = self.client.get(self.url)
        self.assertEqual(res_b.data["kpis"]["products"], 1)
        self.assertEqual(res_b.data["kpis"]["orders"], 1)
        self.assertEqual(res_b.data["orders"]["confirmed"], 0)
        self.assertEqual(res_b.data["orders"]["delivered"], 1)
        self.assertEqual(len(res_b.data["action_orders"]), 0)

    def test_inventory_stock_breakdown(self):
        # Product 1: simple product in stock (stock=20)
        VendorProduct.objects.create(
            vendor=self.vendor_a,
            product=self.master_product_1,
            price=Decimal("100.00"),
            stock=20,
            status="ACTIVE"
        )
        # Product 2: simple product low stock (stock=5)
        VendorProduct.objects.create(
            vendor=self.vendor_a,
            product=self.master_product_2,
            price=Decimal("200.00"),
            stock=5,
            status="ACTIVE"
        )

        self.client.force_authenticate(user=self.user_a)
        res = self.client.get(self.url)
        inv = res.data["inventory"]
        self.assertEqual(inv["total_skus"], 2)
        self.assertEqual(inv["in_stock"], 1)
        self.assertEqual(inv["low_stock"], 1)
        self.assertEqual(inv["out_of_stock"], 0)

    def test_action_order_queue_ordering_and_limit(self):
        master_order = Order.objects.create(
            user=self.customer,
            snapshot_full_name="John Doe",
            snapshot_phone="1234567890",
            snapshot_address_line_1="Line 1",
            snapshot_city="City",
            snapshot_state="State",
            snapshot_postal_code="123456",
            subtotal=Decimal("10000.00"),
            total_amount=Decimal("10000.00")
        )

        orders = []
        for i in range(7):
            vo = VendorOrder.objects.create(
                master_order=master_order,
                vendor=self.vendor_a,
                status=VendorOrder.CONFIRMED if i % 2 == 0 else VendorOrder.PROCESSING,
                subtotal=Decimal("1000.00"),
                total_amount=Decimal("1000.00")
            )
            orders.append(vo)

        self.client.force_authenticate(user=self.user_a)
        res = self.client.get(self.url)
        action_orders = res.data["action_orders"]

        # Max 5 action orders
        self.assertEqual(len(action_orders), 5)
        # Verify oldest first (first created order ID)
        self.assertEqual(action_orders[0]["vendor_order_number"], orders[0].vendor_order_number)

    def test_settlement_summary_calculations(self):
        # Create settled settlement
        s_settled = VendorSettlement.objects.create(
            vendor=self.vendor_a,
            status=VendorSettlement.SETTLED,
            payout_status=VendorSettlement.PAYOUT_PAID,
            period_start=timezone.now().date(),
            period_end=timezone.now().date(),
            gross_amount=Decimal("10000.00"),
            commission_amount=Decimal("1000.00"),
            net_payable=Decimal("9000.00")
        )

        # Create approved pending payout settlement
        s_pending = VendorSettlement.objects.create(
            vendor=self.vendor_a,
            status=VendorSettlement.APPROVED,
            payout_status=VendorSettlement.PAYOUT_UNPAID,
            period_start=timezone.now().date(),
            period_end=timezone.now().date(),
            gross_amount=Decimal("5000.00"),
            commission_amount=Decimal("500.00"),
            net_payable=Decimal("4500.00")
        )

        self.client.force_authenticate(user=self.user_a)
        res = self.client.get(self.url)
        st = res.data["settlement"]

        self.assertEqual(st["settled"], "9000.00")
        self.assertEqual(st["pending_payout"], "4500.00")
        self.assertEqual(st["commission"], "1000.00")
        self.assertEqual(st["total_sales"], "15000.00")

    def test_dashboard_get_is_read_only(self):
        initial_movements = VendorInventoryMovement.objects.count()
        initial_orders = VendorOrder.objects.count()
        initial_settlements = VendorSettlement.objects.count()

        self.client.force_authenticate(user=self.user_a)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.assertEqual(VendorInventoryMovement.objects.count(), initial_movements)
        self.assertEqual(VendorOrder.objects.count(), initial_orders)
        self.assertEqual(VendorSettlement.objects.count(), initial_settlements)

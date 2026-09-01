from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import Address
from apps.cart.models import CartItem
from apps.products.models import Product, ProductVariant, Category
from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant, VendorInventoryMovement
from apps.orders.models import Order, OrderItem, VendorOrder

User = get_user_model()


class VendorOrderManagementTests(APITestCase):

    def setUp(self):
        # 1. Create Users
        self.customer = User.objects.create_user(username="customer", email="customer@example.com", password="password123")
        self.vendor_a_user = User.objects.create_user(username="vendor_a", email="vendor_a@example.com", password="password123")
        self.vendor_b_user = User.objects.create_user(username="vendor_b", email="vendor_b@example.com", password="password123")

        # 2. Create Vendor Profiles
        self.vendor_a = Vendor.objects.create(
            user=self.vendor_a_user,
            store_name="Vendor A Store",
            business_name="Vendor A Corp",
            business_email="vendor_a@example.com",
            business_phone="1234567890",
            status="APPROVED"
        )
        self.vendor_b = Vendor.objects.create(
            user=self.vendor_b_user,
            store_name="Vendor B Store",
            business_name="Vendor B Corp",
            business_email="vendor_b@example.com",
            business_phone="0987654321",
            status="APPROVED"
        )

        # 3. Create Address
        self.address = Address.objects.create(
            user=self.customer,
            full_name="John Doe",
            phone="9876543210",
            address_line_1="123 Street",
            city="Indore",
            state="MP",
            postal_code="452001",
            country="India"
        )

        # 4. Create Category and Master Catalog Products
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product_1 = Product.objects.create(category=self.category, name="iPhone 16", slug="iphone-16", description="Apple Phone", is_active=True)
        self.variant_1a = ProductVariant.objects.create(product=self.product_1, name="Black 128GB", sku="IP16-BLK-128", price=Decimal("70000.00"), stock=100, is_active=True)
        self.variant_1b = ProductVariant.objects.create(product=self.product_1, name="Blue 128GB", sku="IP16-BLU-128", price=Decimal("70000.00"), stock=100, is_active=True)

        self.product_2 = Product.objects.create(category=self.category, name="T-Shirt", slug="t-shirt", description="Cotton Tee", is_active=True)
        self.variant_2 = ProductVariant.objects.create(product=self.product_2, name="M Size", sku="TEE-M", price=Decimal("500.00"), stock=100, is_active=True)

        # 5. Create Vendor Offers (Listings)
        # Vendor A lists variant 1a and variant 2
        self.vp_1a_venda = VendorProduct.objects.create(vendor=self.vendor_a, product=self.product_1, status="ACTIVE", price=Decimal("69000.00"), stock=10)
        self.vpv_1a_venda = VendorProductVariant.objects.create(vendor_product=self.vp_1a_venda, product_variant=self.variant_1a, price=Decimal("68000.00"), stock=10, is_active=True)

        self.vp_2_venda = VendorProduct.objects.create(vendor=self.vendor_a, product=self.product_2, status="ACTIVE", price=Decimal("450.00"), stock=5)
        self.vpv_2_venda = VendorProductVariant.objects.create(vendor_product=self.vp_2_venda, product_variant=self.variant_2, price=Decimal("450.00"), stock=5, is_active=True)

        # Vendor B lists variant 1a (higher price)
        self.vp_1a_vendb = VendorProduct.objects.create(vendor=self.vendor_b, product=self.product_1, status="ACTIVE", price=Decimal("72000.00"), stock=15)
        self.vpv_1a_vendb = VendorProductVariant.objects.create(vendor_product=self.vp_1a_vendb, product_variant=self.variant_1a, price=Decimal("71000.00"), stock=15, is_active=True)

    def test_vendor_offer_integrity_validation(self):
        """CartItem models clean() enforces vendor offer scoping rules."""
        # Clean passes on valid offers
        cart_item = CartItem(
            user=self.customer,
            vendor_product=self.vp_1a_venda,
            vendor_product_variant=self.vpv_1a_venda,
            quantity=2
        )
        cart_item.clean()

        # Reject cross-product combinations
        cart_item_bad = CartItem(
            user=self.customer,
            vendor_product=self.vp_2_venda,
            vendor_product_variant=self.vpv_1a_venda,
            quantity=1
        )
        with self.assertRaises(Exception):
            cart_item_bad.clean()

    def test_multi_vendor_splitting_and_totals(self):
        """Checkout splits a customer cart into sub-orders per vendor."""
        # 1. Customer adds Vendor A's offer and Vendor B's offer to cart
        self.client.force_authenticate(user=self.customer)
        
        CartItem.objects.create(
            user=self.customer,
            vendor_product=self.vp_1a_venda,
            vendor_product_variant=self.vpv_1a_venda,
            quantity=1
        )
        CartItem.objects.create(
            user=self.customer,
            vendor_product=self.vp_1a_vendb,
            vendor_product_variant=self.vpv_1a_vendb,
            quantity=1
        )

        # 2. Checkout POST
        url = reverse("orders:place-order")
        data = {
            "address": self.address.id,
            "payment_method": "cod",
            "idempotency_key": "test_idempotency_123"
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 3. Assert Master and Sub-order creation
        master_order = Order.objects.get(order_number=response.data["order"]["order_number"])
        self.assertEqual(master_order.vendor_orders.count(), 2)

        # Totals verify:
        # Vendor A: vpv_1a_venda price is 68000.00
        # Vendor B: vpv_1a_vendb price is 71000.00
        # Master subtotal: 68000 + 71000 = 139000.00
        self.assertEqual(master_order.subtotal, Decimal("139000.00"))

        sub_orders = list(master_order.vendor_orders.all())
        venda_sub = next(so for so in sub_orders if so.vendor == self.vendor_a)
        vendb_sub = next(so for so in sub_orders if so.vendor == self.vendor_b)

        self.assertEqual(venda_sub.subtotal, Decimal("68000.00"))
        self.assertEqual(vendb_sub.subtotal, Decimal("71000.00"))

        # Assert no orphan items
        for item in master_order.items.all():
            self.assertIsNotNone(item.vendor_order)

    def test_vendor_ownership_scoping_permissions(self):
        """Vendors can only view and update their own sub-orders."""
        # 1. Setup split orders
        master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            subtotal=Decimal("139000.00"),
            total_amount=Decimal("139000.00")
        )
        venda_sub = VendorOrder.objects.create(
            master_order=master_order,
            vendor=self.vendor_a,
            subtotal=Decimal("68000.00"),
            total_amount=Decimal("68000.00")
        )
        vendb_sub = VendorOrder.objects.create(
            master_order=master_order,
            vendor=self.vendor_b,
            subtotal=Decimal("71000.00"),
            total_amount=Decimal("71000.00")
        )

        # 2. Authenticate as Vendor A
        self.client.force_authenticate(user=self.vendor_a_user)
        
        # Vendor A can retrieve its own sub-order
        detail_url = reverse("vendor-order-detail", kwargs={"vendor_order_number": venda_sub.vendor_order_number})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Vendor A receives 404 when querying Vendor B's sub-order
        bad_detail_url = reverse("vendor-order-detail", kwargs={"vendor_order_number": vendb_sub.vendor_order_number})
        response = self.client.get(bad_detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_database_enforced_idempotency(self):
        """Duplicate checkout requests with same idempotency key return original order."""
        self.client.force_authenticate(user=self.customer)
        CartItem.objects.create(
            user=self.customer,
            vendor_product=self.vp_1a_venda,
            vendor_product_variant=self.vpv_1a_venda,
            quantity=1
        )

        url = reverse("orders:place-order")
        data = {
            "address": self.address.id,
            "payment_method": "cod",
            "idempotency_key": "user_idemp_key_999"
        }

        # First request
        response1 = self.client.post(url, data, format="json")
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)

        # Second request (duplicate)
        response2 = self.client.post(url, data, format="json")
        self.assertEqual(response2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response1.data["order"]["order_number"], response2.data["order"]["order_number"])

    def test_status_derived_aggregation(self):
        """Master order status reflects aggregation rules of sub-orders."""
        master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            subtotal=Decimal("139000.00"),
            total_amount=Decimal("139000.00")
        )
        sub1 = VendorOrder.objects.create(
            master_order=master_order,
            vendor=self.vendor_a,
            status=VendorOrder.CONFIRMED
        )
        sub2 = VendorOrder.objects.create(
            master_order=master_order,
            vendor=self.vendor_b,
            status=VendorOrder.CONFIRMED
        )

        # Reconcile
        from apps.orders.services import reconcile_master_order_status
        reconcile_master_order_status(master_order)
        self.assertEqual(master_order.status, Order.CONFIRMED)

        # One becomes processing
        sub1.status = VendorOrder.PROCESSING
        sub1.save()
        reconcile_master_order_status(master_order)
        # We mapped mixed active/processing to PACKED or confirmed.
        self.assertEqual(master_order.status, Order.PACKED)

        # All cancelled
        sub1.status = VendorOrder.CANCELLED
        sub2.status = VendorOrder.CANCELLED
        sub1.save()
        sub2.save()
        reconcile_master_order_status(master_order)
        self.assertEqual(master_order.status, Order.CANCELLED)

    def test_inventory_booking_and_cancellation_reversal(self):
        """Checkout logs OUT movements; cancellation logs compensating IN movements."""
        self.client.force_authenticate(user=self.customer)
        CartItem.objects.create(
            user=self.customer,
            vendor_product=self.vp_1a_venda,
            vendor_product_variant=self.vpv_1a_venda,
            quantity=2
        )

        # 1. Place order
        url = reverse("orders:place-order")
        data = {
            "address": self.address.id,
            "payment_method": "cod"
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        master_order = Order.objects.get(order_number=response.data["order"]["order_number"])
        sub_order = master_order.vendor_orders.first()

        # Assert stock is reserved
        # Initial stock: 10, ordered: 2. Current stock: 8
        self.vpv_1a_venda.refresh_from_db()
        self.assertEqual(self.vpv_1a_venda.stock, 8)

        # Verify inventory movement log OUT is recorded
        movements = list(VendorInventoryMovement.objects.filter(vendor_product_variant=self.vpv_1a_venda))
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].movement_type, "OUT")
        self.assertEqual(movements[0].quantity, -2) # converted to negative OUT delta
        self.assertEqual(movements[0].reason, "OTHER")
        self.assertEqual(movements[0].reference_type, "ORDER")
        self.assertEqual(movements[0].reference_id, master_order.order_number)
        self.assertIn(sub_order.vendor_order_number, movements[0].note)

        # 2. Cancel vendor order
        # Cancel as vendor
        self.client.force_authenticate(user=self.vendor_a_user)
        cancel_url = reverse("vendor-order-cancel", kwargs={"vendor_order_number": sub_order.vendor_order_number})
        response = self.client.post(cancel_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Assert stock is restored to 10
        self.vpv_1a_venda.refresh_from_db()
        self.assertEqual(self.vpv_1a_venda.stock, 10)

        movements = list(VendorInventoryMovement.objects.filter(vendor_product_variant=self.vpv_1a_venda))
        self.assertEqual(len(movements), 2)
        # sorted by -created_at, so index 0 is newest (IN), index 1 is oldest (OUT)
        self.assertEqual(movements[0].movement_type, "IN")
        self.assertEqual(movements[0].quantity, 2)
        self.assertEqual(movements[0].reason, "OTHER")
        self.assertEqual(movements[0].reference_type, "ORDER_CANCELLATION")
        self.assertEqual(movements[0].reference_id, master_order.order_number)
        self.assertIn(sub_order.vendor_order_number, movements[0].note)

        self.assertEqual(movements[1].movement_type, "OUT")
        self.assertEqual(movements[1].quantity, -2)
        self.assertEqual(movements[1].reason, "OTHER")

        # 3. Duplicate cancellation attempts must be rejected and not create another IN movement
        response_dup = self.client.post(cancel_url)
        self.assertEqual(response_dup.status_code, status.HTTP_400_BAD_REQUEST)
        
        movements_after = list(VendorInventoryMovement.objects.filter(vendor_product_variant=self.vpv_1a_venda))
        self.assertEqual(len(movements_after), 2)

    def test_cod_order_starts_pending_and_becomes_paid_on_delivery(self):
        CartItem.objects.create(user=self.customer, vendor_product=self.vpv_1a_venda.vendor_product, vendor_product_variant=self.vpv_1a_venda, quantity=1)
        self.client.force_authenticate(user=self.customer)
        response = self.client.post(reverse("orders:place-order"), {
            "address": self.address.id,
            "payment_method": Order.COD
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        order_id = response.data["order"]["id"]
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.payment_status, Order.PAYMENT_PENDING)

        sub_order = order.vendor_orders.first()
        sub_order.status = VendorOrder.DELIVERED
        sub_order.save()

        from apps.orders.services import reconcile_master_order_status
        reconcile_master_order_status(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.DELIVERED)
        self.assertEqual(order.payment_status, Order.PAYMENT_PAID)

    def test_unpaid_online_order_expiration(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.orders.services import expire_unpaid_orders

        CartItem.objects.create(user=self.customer, vendor_product=self.vpv_1a_venda.vendor_product, vendor_product_variant=self.vpv_1a_venda, quantity=2)
        self.client.force_authenticate(user=self.customer)
        response = self.client.post(reverse("orders:place-order"), {
            "address": self.address.id,
            "payment_method": Order.RAZORPAY
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        order_id = response.data["order"]["id"]
        order = Order.objects.get(pk=order_id)
        order.created_at = timezone.now() - timedelta(minutes=45)
        order.save(update_fields=["created_at"])

        expired_count = expire_unpaid_orders(expiration_minutes=30)
        self.assertEqual(expired_count, 1)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertEqual(order.payment_status, Order.PAYMENT_FAILED)

        # Verify stock was restored
        self.vpv_1a_venda.refresh_from_db()
        self.assertEqual(self.vpv_1a_venda.stock, 10)


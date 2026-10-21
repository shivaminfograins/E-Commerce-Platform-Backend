from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from apps.coupons.models import Coupon, CouponUsage
from apps.products.models import Product, Category, ProductVariant
from apps.cart.models import CartItem
from apps.orders.models import Order
from apps.accounts.models import Address

User = get_user_model()

class CouponModelTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.coupon_pct = Coupon.objects.create(
            code="pct10",
            description="10% Off",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            min_purchase_amount=Decimal("100.00"),
            max_discount_amount=Decimal("50.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True,
            usage_limit=10,
            per_user_limit=1
        )

    def test_coupon_code_uppercased(self):
        """Coupon code must be saved as uppercase and stripped of whitespace."""
        coupon = Coupon.objects.create(
            code="  test-code-50  ",
            discount_value=Decimal("50.00"),
            start_date=self.now,
            end_date=self.now + timedelta(days=1)
        )
        self.assertEqual(coupon.code, "TEST-CODE-50")

    def test_coupon_validity_period(self):
        """Coupon expiry check returns True within start and end date, False otherwise."""
        self.assertTrue(self.coupon_pct.is_valid_now())
        
        self.coupon_pct.end_date = self.now - timedelta(seconds=1)
        self.coupon_pct.save()
        self.assertFalse(self.coupon_pct.is_valid_now())

    def test_calculate_discount_percentage(self):
        """Validate discount calculation for percentage discounts with min purchase and cap."""
        # Minimum purchase not met
        self.assertEqual(self.coupon_pct.calculate_discount(Decimal("50.00")), Decimal("0.00"))
        
        # 10% of 200 = 20
        self.assertEqual(self.coupon_pct.calculate_discount(Decimal("200.00")), Decimal("20.00"))
        
        # 10% of 600 = 60, capped at max_discount_amount=50.00
        self.assertEqual(self.coupon_pct.calculate_discount(Decimal("600.00")), Decimal("50.00"))

    def test_calculate_discount_fixed(self):
        """Validate discount calculation for fixed discounts."""
        fixed_coupon = Coupon.objects.create(
            code="FIXED20",
            discount_type="fixed",
            discount_value=Decimal("20.00"),
            min_purchase_amount=Decimal("10.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1)
        )
        # Min purchase not met
        self.assertEqual(fixed_coupon.calculate_discount(Decimal("5.00")), Decimal("0.00"))
        
        # Normal discount
        self.assertEqual(fixed_coupon.calculate_discount(Decimal("100.00")), Decimal("20.00"))
        
        # Fixed discount cannot exceed subtotal
        self.assertEqual(fixed_coupon.calculate_discount(Decimal("15.00")), Decimal("15.00"))


class CouponAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer",
            email="customer@example.com",
            password="Password123",
            role="customer"
        )
        self.client.force_authenticate(user=self.user)
        
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(name="Smartphone", slug="smartphone", category=self.category)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="Default",
            sku="PHONE-DEF",
            price=Decimal("150.00"),
            stock=100
        )
        
        # Vendor offer setup for multi-vendor cart compatibility
        from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant
        self.v_user = User.objects.create_user(username="coupon_v_user", email="coupon_v@example.com", password="Password123", role="vendor")
        self.vendor = Vendor.objects.create(user=self.v_user, business_name="Coupon Vendor Corp", store_name="Coupon Store", business_email="coupon_v@example.com", business_phone="9876543210", status="APPROVED")
        self.vp = VendorProduct.objects.create(vendor=self.vendor, product=self.product, price=Decimal("150.00"), stock=100, status="ACTIVE")
        self.vpv = VendorProductVariant.objects.create(vendor_product=self.vp, product_variant=self.variant, price=Decimal("150.00"), stock=100, is_active=True)

        # Add to cart
        self.cart_item = CartItem.objects.create(
            user=self.user,
            vendor_product=self.vp,
            vendor_product_variant=self.vpv,
            variant=self.variant,
            quantity=1
        )
        
        self.now = timezone.now()
        self.coupon = Coupon.objects.create(
            code="SAVE10",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            min_purchase_amount=Decimal("100.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )

    def test_validate_coupon_success(self):
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "SAVE10"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(float(response.data["discount_amount"]), 15.0) # 10% of 150.00

    def test_validate_coupon_expired(self):
        self.coupon.end_date = self.now - timedelta(days=1)
        self.coupon.save()
        
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "SAVE10"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])

    def test_validate_coupon_min_purchase(self):
        self.coupon.min_purchase_amount = Decimal("200.00")
        self.coupon.save()
        
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "SAVE10"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])


class CouponOrderIntegrationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer_order",
            email="custorder@example.com",
            password="Password123",
            role="customer"
        )
        self.client.force_authenticate(user=self.user)
        
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(name="Smartphone", slug="smartphone", category=self.category)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="Default",
            sku="PHONE-ORDER",
            price=Decimal("150.00"),
            stock=100
        )
        from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant
        self.v_user = User.objects.create_user(username="coupon_v_user2", email="coupon_v2@example.com", password="Password123", role="vendor")
        self.vendor = Vendor.objects.create(user=self.v_user, business_name="Coupon Vendor Corp 2", store_name="Coupon Store 2", business_email="coupon_v2@example.com", business_phone="9876543211", status="APPROVED")
        self.vp = VendorProduct.objects.create(vendor=self.vendor, product=self.product, price=Decimal("150.00"), stock=100, status="ACTIVE")
        self.vpv = VendorProductVariant.objects.create(vendor_product=self.vp, product_variant=self.variant, price=Decimal("150.00"), stock=100, is_active=True)

        self.cart_item = CartItem.objects.create(
            user=self.user,
            vendor_product=self.vp,
            vendor_product_variant=self.vpv,
            variant=self.variant,
            quantity=1
        )
        self.address = Address.objects.create(
            user=self.user,
            full_name="John Doe",
            phone="1234567890",
            address_line_1="123 Main St",
            city="New York",
            state="NY",
            postal_code="10001",
            country="USA"
        )
        self.now = timezone.now()
        self.coupon = Coupon.objects.create(
            code="ORDER10",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            min_purchase_amount=Decimal("100.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True,
            usage_limit=5,
            per_user_limit=1
        )

    def test_place_order_with_coupon_success(self):
        url = "/api/orders/"
        payload = {
            "address": self.address.id,
            "payment_method": "cod",
            "coupon_code": "ORDER10"
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        
        order = Order.objects.get(order_number=response.data["order"]["order_number"])
        self.assertEqual(order.coupon_code, "ORDER10")
        self.assertEqual(order.discount, Decimal("15.00"))
        self.assertEqual(order.total_amount, Decimal("234.00")) # 150 + 99 - 15
        
        # Verify coupon usage is incremented and CouponUsage logged
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.usage_count, 1)
        self.assertTrue(CouponUsage.objects.filter(coupon=self.coupon, user=self.user, order=order).exists())

    def test_place_order_with_coupon_per_user_limit_exceeded(self):
        # Place first order
        url = "/api/orders/"
        payload = {
            "address": self.address.id,
            "payment_method": "cod",
            "coupon_code": "ORDER10"
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Add item to cart again for second order
        CartItem.objects.create(
            user=self.user,
            variant=self.variant,
            quantity=1
        )
        
        # Attempt to place order again with same coupon
        response2 = self.client.post(url, payload)
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response2.data["success"])


class CouponEngineNewFeaturesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="engine_customer",
            email="engine@example.com",
            password="Password123",
            role="customer"
        )
        self.client.force_authenticate(user=self.user)
        
        self.category_elec = Category.objects.create(name="Electronics", slug="electronics")
        self.category_book = Category.objects.create(name="Books", slug="books")
        
        self.product_phone = Product.objects.create(name="Phone", slug="phone", category=self.category_elec)
        self.product_novel = Product.objects.create(name="Novel", slug="novel", category=self.category_book)
        
        self.variant_phone = ProductVariant.objects.create(
            product=self.product_phone, name="Default", sku="PH-1", price=Decimal("100.00"), stock=100
        )
        self.variant_novel = ProductVariant.objects.create(
            product=self.product_novel, name="Default", sku="NV-1", price=Decimal("50.00"), stock=100
        )

        from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant
        self.v_user = User.objects.create_user(username="coupon_v_user3", email="coupon_v3@example.com", password="Password123", role="vendor")
        self.vendor = Vendor.objects.create(user=self.v_user, business_name="Coupon Vendor Corp 3", store_name="Coupon Store 3", business_email="coupon_v3@example.com", business_phone="9876543212", status="APPROVED")
        
        self.vp_phone = VendorProduct.objects.create(vendor=self.vendor, product=self.product_phone, price=Decimal("100.00"), stock=100, status="ACTIVE")
        self.vpv_phone = VendorProductVariant.objects.create(vendor_product=self.vp_phone, product_variant=self.variant_phone, price=Decimal("100.00"), stock=100, is_active=True)

        self.vp_novel = VendorProduct.objects.create(vendor=self.vendor, product=self.product_novel, price=Decimal("50.00"), stock=100, status="ACTIVE")
        self.vpv_novel = VendorProductVariant.objects.create(vendor_product=self.vp_novel, product_variant=self.variant_novel, price=Decimal("50.00"), stock=100, is_active=True)
        
        self.now = timezone.now()

    def test_coupon_max_purchase_amount(self):
        # Cart total: 100
        CartItem.objects.create(user=self.user, vendor_product=self.vp_phone, vendor_product_variant=self.vpv_phone, variant=self.variant_phone, quantity=1)
        coupon = Coupon.objects.create(
            code="MAX150",
            discount_type="fixed",
            discount_value=Decimal("10.00"),
            max_purchase_amount=Decimal("80.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "MAX150"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])

    def test_coupon_first_order_only(self):
        CartItem.objects.create(user=self.user, vendor_product=self.vp_phone, vendor_product_variant=self.vpv_phone, variant=self.variant_phone, quantity=1)
        coupon = Coupon.objects.create(
            code="FIRSTONLY",
            discount_type="fixed",
            discount_value=Decimal("10.00"),
            first_order_only=True,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        # Should succeed for first order
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "FIRSTONLY"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        # Create dummy order for user
        address = Address.objects.create(
            user=self.user, full_name="John", phone="123", address_line_1="A", city="B", state="C", postal_code="1", country="USA"
        )
        Order.objects.create(
            user=self.user,
            address=address,
            payment_method="cod",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00")
        )
        
        # Should now fail as not first order
        response = self.client.post(url, {"code": "FIRSTONLY"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])

    def test_coupon_category_scoped(self):
        # 1 Phone (100) + 1 Novel (50) = 150 subtotal
        CartItem.objects.create(user=self.user, vendor_product=self.vp_phone, vendor_product_variant=self.vpv_phone, variant=self.variant_phone, quantity=1)
        CartItem.objects.create(user=self.user, vendor_product=self.vp_novel, vendor_product_variant=self.vpv_novel, variant=self.variant_novel, quantity=1)
        
        coupon = Coupon.objects.create(
            code="ELEC10",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            coupon_scope="specific_categories",
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        coupon.categories.add(self.category_elec)
        
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "ELEC10"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 10% of Phone(100) only = 10, not 15 (10% of 150)
        self.assertEqual(float(response.data["discount_amount"]), 10.0)

    def test_coupon_product_scoped(self):
        CartItem.objects.create(user=self.user, vendor_product=self.vp_phone, vendor_product_variant=self.vpv_phone, variant=self.variant_phone, quantity=1)
        CartItem.objects.create(user=self.user, vendor_product=self.vp_novel, vendor_product_variant=self.vpv_novel, variant=self.variant_novel, quantity=1)
        
        coupon = Coupon.objects.create(
            code="PHONEONLY",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            coupon_scope="specific_products",
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        # Use through model CouponProduct explicitly
        from apps.coupons.models import CouponProduct
        CouponProduct.objects.create(coupon=coupon, product=self.product_phone, min_quantity=1)
        
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "PHONEONLY"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 10% of Phone(100) = 10
        self.assertEqual(float(response.data["discount_amount"]), 10.0)

    def test_coupon_product_combination(self):
        CartItem.objects.create(user=self.user, vendor_product=self.vp_phone, vendor_product_variant=self.vpv_phone, variant=self.variant_phone, quantity=1)
        coupon = Coupon.objects.create(
            code="COMBO",
            discount_type="fixed",
            discount_value=Decimal("20.00"),
            coupon_scope="product_combination",
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        from apps.coupons.models import CouponProduct
        CouponProduct.objects.create(coupon=coupon, product=self.product_phone, min_quantity=1)
        CouponProduct.objects.create(coupon=coupon, product=self.product_novel, min_quantity=1)

        url = reverse("coupon-validate")
        # Fails because Novel is not in the cart
        response = self.client.post(url, {"code": "COMBO"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Add Novel to cart
        CartItem.objects.create(user=self.user, vendor_product=self.vp_novel, vendor_product_variant=self.vpv_novel, variant=self.variant_novel, quantity=1)
        response = self.client.post(url, {"code": "COMBO"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

    def test_coupon_free_shipping(self):
        CartItem.objects.create(user=self.user, variant=self.variant_novel, quantity=1) # 50.00 subtotal
        coupon = Coupon.objects.create(
            code="FREESHIP",
            discount_type="free_shipping",
            discount_value=Decimal("0.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=1),
            is_active=True
        )
        url = reverse("coupon-validate")
        response = self.client.post(url, {"code": "FREESHIP"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data["discount_amount"]), 99.0) # shipping charge is 99


class VendorCouponTests(APITestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user_vendor_a = User.objects.create_user(
            username="vendor_a_user", email="vendora@example.com", password="Password123", role="vendor"
        )
        from apps.vendors.models import Vendor
        self.vendor_a = Vendor.objects.create(
            user=self.user_vendor_a, store_name="Vendor A Store", business_name="Vendor A LLC", business_email="vendora@example.com", status="APPROVED", commission_rate=Decimal("10.00")
        )

        self.user_vendor_b = User.objects.create_user(
            username="vendor_b_user", email="vendorb@example.com", password="Password123", role="vendor"
        )
        self.vendor_b = Vendor.objects.create(
            user=self.user_vendor_b, store_name="Vendor B Store", business_name="Vendor B LLC", business_email="vendorb@example.com", status="APPROVED", commission_rate=Decimal("15.00")
        )

        self.customer = User.objects.create_user(
            username="customer_user", email="cust@example.com", password="Password123", role="customer"
        )

        self.category = Category.objects.create(name="Tech", slug="tech")
        self.product_a = Product.objects.create(name="Gadget A", slug="gadget-a", category=self.category)
        self.variant_a = ProductVariant.objects.create(product=self.product_a, name="Std A", sku="SKU-A", price=Decimal("1000.00"))
        
        self.product_b = Product.objects.create(name="Gadget B", slug="gadget-b", category=self.category)
        self.variant_b = ProductVariant.objects.create(product=self.product_b, name="Std B", sku="SKU-B", price=Decimal("500.00"))

        from apps.vendors.models import VendorProduct, VendorProductVariant
        self.vp_a = VendorProduct.objects.create(vendor=self.vendor_a, product=self.product_a, price=Decimal("1000.00"), status="ACTIVE")
        self.vpv_a = VendorProductVariant.objects.create(vendor_product=self.vp_a, product_variant=self.variant_a, price=Decimal("1000.00"), stock=100, is_active=True)

        self.vp_b = VendorProduct.objects.create(vendor=self.vendor_b, product=self.product_b, price=Decimal("500.00"), status="ACTIVE")
        self.vpv_b = VendorProductVariant.objects.create(vendor_product=self.vp_b, product_variant=self.variant_b, price=Decimal("500.00"), stock=100, is_active=True)

    def test_vendor_create_coupon(self):
        """Vendor can create a coupon via vendor API which is automatically VENDOR_FUNDED and bound to vendor."""
        self.client.force_authenticate(user=self.user_vendor_a)
        url = reverse("vendor-coupons-list")
        payload = {
            "code": "VENDOR100",
            "description": "Vendor A 100 Off",
            "discount_type": "fixed",
            "discount_value": "100.00",
            "min_purchase_amount": "500.00",
            "start_date": (self.now - timedelta(days=1)).isoformat(),
            "end_date": (self.now + timedelta(days=10)).isoformat(),
            "is_active": True
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["funding_type"], "VENDOR_FUNDED")
        self.assertEqual(response.data["vendor_name"], "Vendor A Store")

    def test_vendor_tenant_isolation(self):
        """Vendor B cannot view or modify Vendor A's coupon."""
        coupon_a = Coupon.objects.create(
            code="VENDORA50",
            discount_type="fixed",
            discount_value=Decimal("50.00"),
            vendor=self.vendor_a,
            funding_type=Coupon.FUNDING_VENDOR,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=10),
            is_active=True
        )
        self.client.force_authenticate(user=self.user_vendor_b)
        url = reverse("vendor-coupons-detail", kwargs={"pk": coupon_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_mixed_vendor_cart_coupon_allocation(self):
        """Vendor A coupon applied on mixed-vendor cart allocates 100% discount to Vendor A and $0 to Vendor B."""
        coupon_a = Coupon.objects.create(
            code="VENDORA100",
            discount_type="fixed",
            discount_value=Decimal("100.00"),
            vendor=self.vendor_a,
            funding_type=Coupon.FUNDING_VENDOR,
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=10),
            is_active=True
        )

        self.client.force_authenticate(user=self.customer)
        CartItem.objects.create(
            user=self.customer, vendor_product=self.vp_a, vendor_product_variant=self.vpv_a, variant=self.variant_a, quantity=1
        )
        CartItem.objects.create(
            user=self.customer, vendor_product=self.vp_b, vendor_product_variant=self.vpv_b, variant=self.variant_b, quantity=1
        )

        from apps.orders.models import Order
        from apps.orders.services import create_split_vendor_orders
        master_order = Order.objects.create(
            user=self.customer,
            subtotal=Decimal("1500.00"),
            discount=Decimal("100.00"),
            total_amount=Decimal("1400.00"),
            coupon=coupon_a,
            coupon_code=coupon_a.code
        )

        cart_items = list(CartItem.objects.filter(user=self.customer))
        create_split_vendor_orders(master_order, cart_items, self.customer)

        from apps.orders.models import VendorOrder
        v_order_a = VendorOrder.objects.get(master_order=master_order, vendor=self.vendor_a)
        v_order_b = VendorOrder.objects.get(master_order=master_order, vendor=self.vendor_b)

        self.assertEqual(v_order_a.subtotal, Decimal("1000.00"))
        self.assertEqual(v_order_a.discount, Decimal("100.00"))
        self.assertEqual(v_order_a.vendor_discount_amount, Decimal("100.00"))
        self.assertEqual(v_order_a.platform_discount_amount, Decimal("0.00"))
        self.assertEqual(v_order_a.discount_funding_type, "VENDOR_FUNDED")

        self.assertEqual(v_order_b.subtotal, Decimal("500.00"))
        self.assertEqual(v_order_b.discount, Decimal("0.00"))
        self.assertEqual(v_order_b.vendor_discount_amount, Decimal("0.00"))
        self.assertEqual(v_order_b.platform_discount_amount, Decimal("0.00"))
        self.assertEqual(v_order_b.discount_funding_type, "NONE")

        # Now verify settlement generation for Vendor A
        from apps.settlements.services import generate_vendor_settlements
        from apps.orders.models import VendorOrder, Order
        master_order.payment_status = Order.PAYMENT_PAID
        master_order.save()
        v_order_a.status = VendorOrder.DELIVERED
        v_order_a.delivered_at = self.now - timedelta(days=10)
        v_order_a.save()

        today = self.now.date()
        settlement_a = generate_vendor_settlements(
            vendor=self.vendor_a,
            period_start=today - timedelta(days=30),
            period_end=today + timedelta(days=1),
            user=self.user_vendor_a
        )
        self.assertIsNotNone(settlement_a)
        item_a = settlement_a.items.first()
        self.assertEqual(item_a.gross_amount, Decimal("1000.00"))
        self.assertEqual(item_a.vendor_discount_amount, Decimal("100.00"))
        self.assertEqual(item_a.commission_amount, Decimal("100.00")) # 10% of 1000.00 gross
        self.assertEqual(item_a.net_payable, Decimal("800.00")) # 1000 - 100(discount) - 100(comm)

    def test_used_coupon_deletion_blocked(self):
        """Used coupon physical deletion is blocked with ValidationError."""
        coupon = Coupon.objects.create(
            code="USED100",
            discount_type="fixed",
            discount_value=Decimal("100.00"),
            start_date=self.now - timedelta(days=1),
            end_date=self.now + timedelta(days=10),
            is_active=True
        )
        from apps.orders.models import Order
        order = Order.objects.create(
            user=self.customer,
            subtotal=Decimal("500.00"),
            total_amount=Decimal("400.00")
        )
        CouponUsage.objects.create(coupon=coupon, user=self.customer, order=order)

        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            coupon.delete()



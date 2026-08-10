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
        # Add to cart
        self.cart_item = CartItem.objects.create(
            user=self.user,
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
        self.cart_item = CartItem.objects.create(
            user=self.user,
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
        
        self.now = timezone.now()

    def test_coupon_max_purchase_amount(self):
        # Cart total: 100
        CartItem.objects.create(user=self.user, variant=self.variant_phone, quantity=1)
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
        CartItem.objects.create(user=self.user, variant=self.variant_phone, quantity=1)
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
        CartItem.objects.create(user=self.user, variant=self.variant_phone, quantity=1)
        CartItem.objects.create(user=self.user, variant=self.variant_novel, quantity=1)
        
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
        CartItem.objects.create(user=self.user, variant=self.variant_phone, quantity=1)
        CartItem.objects.create(user=self.user, variant=self.variant_novel, quantity=1)
        
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
        CartItem.objects.create(user=self.user, variant=self.variant_phone, quantity=1)
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
        CartItem.objects.create(user=self.user, variant=self.variant_novel, quantity=1)
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


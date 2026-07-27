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

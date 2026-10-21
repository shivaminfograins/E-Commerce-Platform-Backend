from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from apps.products.models import Category, Brand, Product, ProductVariant
from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant
from apps.cart.models import CartItem, Guest
from apps.cart.serializers import CartItemSerializer

User = get_user_model()


class CartRemediationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="cart_user", email="cart@example.com", password="password123")
        self.vendor_user = User.objects.create_user(username="vendor_user", email="vendor@example.com", password="password123", role="vendor")
        
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            business_name="Test Business",
            store_name="Test Store",
            business_email="vendor@example.com",
            business_phone="9999999999",
            status="APPROVED"
        )
        self.category = Category.objects.create(name="Tech", slug="tech")
        self.brand = Brand.objects.create(name="BrandX", slug="brandx")

        # Simple Product (no variants)
        self.simple_product = Product.objects.create(name="Simple Cable", slug="simple-cable", category=self.category, brand=self.brand, is_active=True)
        self.vp_simple = VendorProduct.objects.create(vendor=self.vendor, product=self.simple_product, price=150.00, stock=50, status="ACTIVE")

        # Variant Product
        self.var_product = Product.objects.create(name="Phone", slug="phone", category=self.category, brand=self.brand, is_active=True)
        self.m_variant = ProductVariant.objects.create(product=self.var_product, name="128GB", sku="PHONE-128GB", price=1000.00, stock=50)
        self.vp_var = VendorProduct.objects.create(vendor=self.vendor, product=self.var_product, status="ACTIVE")
        self.vpv = VendorProductVariant.objects.create(vendor_product=self.vp_var, product_variant=self.m_variant, price=950.00, stock=20, vendor_sku="VSKU-PHONE-128", is_active=True)

    def test_simple_product_cart_addition_and_serialization(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/", {
            "vendor_product": self.vp_simple.id,
            "quantity": 2
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        items = response.data["cart_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["product_name"], "Simple Cable")
        self.assertEqual(items[0]["sku"], "simple-cable")
        self.assertEqual(items[0]["price"], "150.00")

    def test_variant_product_cart_addition_and_sku_preservation(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/cart/", {
            "vendor_product_variant": self.vpv.id,
            "quantity": 1
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        items = response.data["cart_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["variant_name"], "128GB")
        self.assertEqual(items[0]["sku"], "VSKU-PHONE-128")
        self.assertEqual(items[0]["price"], "950.00")

    def test_cart_item_update_and_delete_using_cart_item_pk(self):
        self.client.force_authenticate(user=self.user)
        add_res = self.client.post("/api/cart/", {
            "vendor_product": self.vp_simple.id,
            "quantity": 1
        })
        cart_item_id = add_res.data["cart_items"][0]["id"]

        # Update quantity using cart_item_id PK
        patch_res = self.client.patch(f"/api/cart/{cart_item_id}/", {"quantity": 5})
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_res.data["cart_items"][0]["quantity"], 5)

        # Delete item using cart_item_id PK
        del_res = self.client.delete(f"/api/cart/{cart_item_id}/")
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)
        self.assertEqual(del_res.data["item_count"], 0)

    def test_guest_cart_merge(self):
        guest = Guest.objects.create(guest_token="guest-token-123")
        CartItem.objects.create(guest=guest, vendor_product=self.vp_simple, quantity=3)

        self.client.force_authenticate(user=self.user)
        merge_res = self.client.post("/api/cart/merge/", {"guest_id": "guest-token-123"})
        self.assertEqual(merge_res.status_code, status.HTTP_200_OK)
        self.assertEqual(merge_res.data["item_count"], 1)
        self.assertEqual(merge_res.data["cart_items"][0]["quantity"], 3)

    def test_cart_addition_with_product_id_and_variant_id(self):
        self.client.force_authenticate(user=self.user)

        # Test adding simple product using product_id
        res_prod = self.client.post("/api/cart/", {
            "product_id": self.simple_product.id,
            "quantity": 1
        })
        self.assertEqual(res_prod.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_prod.data["cart_items"][0]["product_name"], "Simple Cable")

        # Clear cart
        self.client.delete("/api/cart/clear/")

        # Test adding variant product using master variant_id
        res_var = self.client.post("/api/cart/", {
            "variant_id": self.m_variant.id,
            "quantity": 2
        })
        self.assertEqual(res_var.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_var.data["cart_items"][0]["variant_name"], "128GB")


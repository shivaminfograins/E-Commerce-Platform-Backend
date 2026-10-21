from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.test import APITestCase
from apps.products.models import Product, Category, Brand
from apps.vendors.models import Vendor, VendorProduct, VendorProductVariant

User = get_user_model()

class VendorOwnedVariantsRegressionTests(APITestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(username="vendor1", email="vendor1@example.com", password="password123", role="VENDOR")
        self.vendor1 = Vendor.objects.create(user=self.user1, store_name="Vendor One Store", business_email="v1@example.com", status="APPROVED")

        self.user2 = User.objects.create_user(username="vendor2", email="vendor2@example.com", password="password123", role="VENDOR")
        self.vendor2 = Vendor.objects.create(user=self.user2, store_name="Vendor Two Store", business_email="v2@example.com", status="APPROVED")

        self.admin_user = User.objects.create_superuser(username="admin", email="admin@example.com", password="password123", role="ADMIN")

        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.brand = Brand.objects.create(name="TechCorp", slug="techcorp")

        self.master_product = Product.objects.create(
            name="Smart Watch",
            slug="smart-watch",
            category=self.category,
            brand=self.brand,
            is_active=True
        )

        self.client.force_authenticate(user=self.user1)

    def test_vendor_can_create_custom_owned_variant(self):
        url = "/api/vendors/products/"
        payload = {
            "product": self.master_product.id,
            "status": "ACTIVE",
            "vendor_variants": [
                {
                    "variant_name": "Black / 44mm",
                    "attributes": {"Color": "Black", "Size": "44mm"},
                    "price": "199.99",
                    "stock": 25,
                    "vendor_sku": "VW-BLK-44",
                    "is_active": True
                }
            ]
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        vp = VendorProduct.objects.get(vendor=self.vendor1, product=self.master_product)
        self.assertEqual(vp.vendor_variants.count(), 1)
        vpv = vp.vendor_variants.first()
        self.assertEqual(vpv.variant_name, "Black / 44mm")
        self.assertEqual(vpv.attributes, {"Color": "Black", "Size": "44mm"})
        self.assertEqual(vpv.price, Decimal("199.99"))
        self.assertEqual(vpv.stock, 25)
        self.assertIsNone(vpv.product_variant)
        self.assertTrue(vpv.is_effectively_active)

    def test_master_product_freeze_deactivates_vendor_offers_effectively(self):
        vp = VendorProduct.objects.create(vendor=self.vendor1, product=self.master_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(
            vendor_product=vp,
            variant_name="Silver / 40mm",
            attributes={"Color": "Silver"},
            price=Decimal("179.99"),
            stock=10,
            is_active=True
        )

        self.assertTrue(vp.is_effectively_active)
        self.assertTrue(vpv.is_effectively_active)

        # Admin deactivates Master Product
        self.client.force_authenticate(user=self.admin_user)
        deactivate_url = f"/api/admin/products/{self.master_product.id}/deactivate/"
        res = self.client.patch(deactivate_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.master_product.refresh_from_db()
        vp.refresh_from_db()
        vpv.refresh_from_db()

        self.assertFalse(self.master_product.is_active)
        self.assertEqual(vp.status, "ACTIVE")
        self.assertFalse(vp.is_effectively_active)
        self.assertFalse(vpv.is_effectively_active)

        # Admin reactivates Master Product
        activate_url = f"/api/admin/products/{self.master_product.id}/activate/"
        res_act = self.client.patch(activate_url)
        self.assertEqual(res_act.status_code, status.HTTP_200_OK)

        self.master_product.refresh_from_db()
        vp.refresh_from_db()
        vpv.refresh_from_db()

        self.assertTrue(self.master_product.is_active)
        self.assertTrue(vp.is_effectively_active)
        self.assertTrue(vpv.is_effectively_active)

    def test_admin_product_detail_vendor_summary_and_variant_breakdown(self):
        vp1 = VendorProduct.objects.create(vendor=self.vendor1, product=self.master_product, status="ACTIVE")
        vpv1 = VendorProductVariant.objects.create(
            vendor_product=vp1,
            variant_name="Black / 44mm",
            price=Decimal("199.99"),
            stock=15
        )

        vp2 = VendorProduct.objects.create(vendor=self.vendor2, product=self.master_product, status="INACTIVE")
        vpv2 = VendorProductVariant.objects.create(
            vendor_product=vp2,
            variant_name="Gold / 40mm",
            price=Decimal("219.99"),
            stock=5
        )

        self.client.force_authenticate(user=self.admin_user)
        detail_url = f"/api/admin/products/{self.master_product.id}/"
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        self.assertIn("vendor_summary", data)
        summary = data["vendor_summary"]
        self.assertEqual(summary["total_vendors"], 2)
        self.assertEqual(summary["active_vendors"], 1)
        self.assertEqual(summary["total_variants"], 2)

        self.assertIn("vendors", data)
        self.assertEqual(len(data["vendors"]), 2)

    def test_tenant_isolation_vendor_cannot_modify_other_vendor_variants(self):
        vp2 = VendorProduct.objects.create(vendor=self.vendor2, product=self.master_product, status="ACTIVE")
        vpv2 = VendorProductVariant.objects.create(
            vendor_product=vp2,
            variant_name="Rose Gold",
            price=Decimal("249.99"),
            stock=8
        )

        self.client.force_authenticate(user=self.user1)

        detail_url = f"/api/vendors/products/{vp2.id}/"
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        patch_url = f"/api/vendors/products/{vp2.id}/"
        update_res = self.client.patch(patch_url, {"status": "INACTIVE"}, format="json")
        self.assertEqual(update_res.status_code, status.HTTP_404_NOT_FOUND)

    def test_cart_item_blocked_if_master_product_frozen(self):
        vp = VendorProduct.objects.create(vendor=self.vendor1, product=self.master_product, status="ACTIVE")
        vpv = VendorProductVariant.objects.create(
            vendor_product=vp,
            variant_name="Blue / S",
            price=Decimal("99.99"),
            stock=10
        )

        self.master_product.is_active = False
        self.master_product.save()

        customer = User.objects.create_user(username="customer", email="customer@example.com", password="password123", role="CUSTOMER")
        self.client.force_authenticate(user=customer)

        cart_url = "/api/cart/"
        payload = {
            "vendor_product_id": vp.id,
            "vendor_product_variant_id": vpv.id,
            "quantity": 1
        }
        res = self.client.post(cart_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

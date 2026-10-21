from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from apps.products.models import Category, Brand, Product
from apps.vendors.models import Vendor, VendorProductRequest

User = get_user_model()

class VendorProductRequestTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(username="adminreq", email="adminreq@test.com", password="adminpass", role="ADMIN")
        self.vendor_user = User.objects.create_user(username="vendorreq", email="vendorreq@test.com", password="vendorpass", role="VENDOR")
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Tech Store Req",
            business_email="techreq@test.com",
            status="APPROVED"
        )
        self.category = Category.objects.create(name="Electronics Req", slug="electronics-req")
        self.brand = Brand.objects.create(name="Sony Req", slug="sony-req")
        
        self.vendor_client = APIClient()
        self.vendor_client.force_authenticate(user=self.vendor_user)

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.admin_user)

    def test_vendor_can_submit_product_request(self):
        url = "/api/vendors/product-requests/"
        data = {
            "product_name": "WH-1000XM5 Headphones",
            "category": self.category.id,
            "brand": self.brand.id,
            "description": "Noise canceling headphones"
        }
        res = self.vendor_client.post(url, data, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], "PENDING")
        self.assertEqual(res.data["product_name"], "WH-1000XM5 Headphones")

    def test_admin_can_approve_product_request(self):
        req = VendorProductRequest.objects.create(
            vendor=self.vendor,
            product_name="PlayStation 5 Pro",
            category=self.category,
            brand=self.brand,
            description="Next gen gaming console",
            status="PENDING"
        )

        approve_url = f"/api/admin/product-requests/{req.id}/approve/"
        res = self.admin_client.post(approve_url, {"admin_notes": "Approved for marketplace"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        req.refresh_from_db()
        self.assertEqual(req.status, "APPROVED")
        self.assertIsNotNone(req.created_master_product)
        self.assertEqual(req.created_master_product.name, "PlayStation 5 Pro")
        self.assertEqual(req.created_master_product.category, self.category)
        
        # Verify vendor can now select this product
        cat_url = f"/api/vendors/catalog/products/{req.created_master_product.id}/"
        cat_res = self.vendor_client.get(cat_url)
        self.assertEqual(cat_res.status_code, status.HTTP_200_OK)
        self.assertEqual(cat_res.data["name"], "PlayStation 5 Pro")

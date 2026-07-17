from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.accounts.models import User, Address
from apps.products.models import Product, Category, ProductVariant, ProductVariantImage
from apps.orders.models import Order, OrderItem


class AdminPanelPhase1Tests(APITestCase):
    def setUp(self):
        # Create users
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            username="adminuser",
            password="adminpassword123",
            role="admin",
            is_verified=True
        )
        self.customer_user = User.objects.create_user(
            email="customer@example.com",
            username="customeruser",
            password="customerpassword123",
            role="customer",
            is_verified=True
        )
        
        # Create categories and products
        self.category = Category.objects.create(
            name="Electronics",
            slug="electronics",
            description="Electronic devices"
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Smartphone",
            slug="smartphone",
            description="Latest smartphone model"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="128GB Black",
            sku="SM-128-BLK",
            price=Decimal("49999.00"),
            stock=15
        )

        # URLs
        self.login_url = reverse("admin_login")
        self.dashboard_url = reverse("admin_dashboard")

        # Create Address
        self.address = Address.objects.create(
            user=self.customer_user,
            full_name="Customer User",
            phone="9876543210",
            address_line_1="123 Main St",
            city="Mumbai",
            state="Maharashtra",
            country="India",
            postal_code="400001",
            is_default=True
        )

    def test_admin_login_success(self):
        response = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertEqual(response.data["user"]["role"], "admin")

    def test_admin_login_failed_for_customer(self):
        response = self.client.post(
            self.login_url,
            {"email": "customer@example.com", "password": "customerpassword123"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("access", response.data)

    def test_dashboard_permission_denied_for_unauthenticated(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_dashboard_permission_denied_for_customer(self):
        # Login as customer
        login_res = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "customer@example.com", "password": "customerpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_dashboard_success_for_admin(self):
        # Login as admin
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_products"], 1)
        self.assertEqual(response.data["total_categories"], 1)
        self.assertEqual(response.data["total_customers"], 1)
        self.assertEqual(len(response.data["low_stock_products"]), 0) # Stock is 15, which is > 10

    def test_category_list_success(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("admin-categories-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_category_create_success(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.post(
            reverse("admin-categories-list"),
            {"name": "Home Appliances", "slug": "home-appliances", "description": "Appliances for home"},
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Category.objects.count(), 2)

    def test_category_toggle_status_success(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertTrue(self.category.is_active)
        response = self.client.patch(
            reverse("admin-categories-toggle-status", kwargs={"pk": self.category.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

    def test_product_list_and_create(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # List
        response = self.client.get(reverse("admin-products-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Create
        create_res = self.client.post(
            reverse("admin-products-list"),
            {
                "category": self.category.id,
                "name": "Tablet Pro",
                "slug": "tablet-pro",
                "description": "High performance tablet",
                "is_active": True
            },
            format="json"
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.count(), 2)

    def test_product_toggle_status(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertTrue(self.product.is_active)
        response = self.client.patch(
            reverse("admin-products-toggle-status", kwargs={"pk": self.product.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_variant_list_create_update_delete(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # List variants for a product
        list_url = reverse("admin-product-variants", kwargs={"product_id": self.product.id})
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        # Create variant
        create_res = self.client.post(
            list_url,
            {
                "name": "256GB Gold",
                "sku": "SM-256-GLD",
                "price": "54999.00",
                "stock": 20
            },
            format="json"
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        new_variant_id = create_res.data["id"]

        # Update variant
        update_url = reverse("admin-variants-detail", kwargs={"pk": new_variant_id})
        update_res = self.client.put(
            update_url,
            {
                "product": self.product.id,
                "name": "256GB Rose Gold",
                "sku": "SM-256-GLD",
                "price": "53999.00",
                "stock": 22
            },
            format="json"
        )
        self.assertEqual(update_res.status_code, status.HTTP_200_OK)
        self.assertEqual(update_res.data["name"], "256GB Rose Gold")

        # Delete variant
        delete_res = self.client.delete(update_url)
        self.assertEqual(delete_res.status_code, status.HTTP_204_NO_CONTENT)

    def test_image_upload_and_delete(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Create dummy image
        dummy_image = SimpleUploadedFile(
            name="test_image.jpg",
            content=b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x05\x04\x04\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b",
            content_type="image/jpeg"
        )

        image_url = reverse("admin-product-images", kwargs={"product_id": self.product.id})
        response = self.client.post(
            image_url,
            {"image": dummy_image, "alt_text": "Smartphone View"},
            format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_image_id = response.data["id"]

        # Delete image
        delete_url = reverse("admin-images-detail", kwargs={"pk": new_image_id})
        delete_res = self.client.delete(delete_url)
        self.assertEqual(delete_res.status_code, status.HTTP_204_NO_CONTENT)

    def test_customer_list_block_unblock(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # List
        response = self.client.get(reverse("admin-customers-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify pagination structure is returned
        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 1)

        # Block customer
        block_url = reverse("admin-customers-block", kwargs={"pk": self.customer_user.id})
        block_res = self.client.patch(block_url)
        self.assertEqual(block_res.status_code, status.HTTP_200_OK)
        self.customer_user.refresh_from_db()
        self.assertFalse(self.customer_user.is_active)

        # Unblock customer
        unblock_url = reverse("admin-customers-unblock", kwargs={"pk": self.customer_user.id})
        unblock_res = self.client.patch(unblock_url)
        self.assertEqual(unblock_res.status_code, status.HTTP_200_OK)
        self.customer_user.refresh_from_db()
        self.assertTrue(self.customer_user.is_active)

    def test_customer_addresses_and_orders(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Fetch customer addresses
        addr_url = reverse("admin-customers-addresses", kwargs={"pk": self.customer_user.id})
        response = self.client.get(addr_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["city"], "Mumbai")

        # Fetch customer orders
        orders_url = reverse("admin-customers-orders", kwargs={"pk": self.customer_user.id})
        orders_res = self.client.get(orders_url)
        self.assertEqual(orders_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(orders_res.data), 0)

    def test_address_list(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # List all addresses
        response = self.client.get(reverse("admin-addresses-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_order_list_retrieve_status_cancel(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Create Order
        order = Order.objects.create(
            user=self.customer_user,
            address=self.address,
            order_number="ORD-TEST-0001",
            status=Order.PENDING,
            payment_method=Order.COD,
            payment_status=Order.PAYMENT_PENDING,
            snapshot_full_name="Customer User",
            snapshot_phone="9876543210",
            snapshot_address_line_1="123 Main St",
            snapshot_city="Mumbai",
            snapshot_state="Maharashtra",
            snapshot_postal_code="400001",
            subtotal=Decimal("49999.00"),
            total_amount=Decimal("49999.00")
        )

        order_item = OrderItem.objects.create(
            order=order,
            product=self.product,
            variant=self.variant,
            product_name="Smartphone",
            variant_name="128GB Black",
            sku="SM-128-BLK",
            price=Decimal("49999.00"),
            quantity=1,
            total=Decimal("49999.00")
        )

        # 1. List
        list_res = self.client.get(reverse("admin-orders-list"))
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertIn("results", list_res.data)
        self.assertEqual(len(list_res.data["results"]), 1)

        # 2. Retrieve detail
        detail_res = self.client.get(reverse("admin-orders-detail", kwargs={"pk": order.id}))
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        self.assertIn("order", detail_res.data)
        self.assertEqual(detail_res.data["order"]["order_number"], "ORD-TEST-0001")

        # 3. Update status
        status_url = reverse("admin-orders-status", kwargs={"pk": order.id})
        status_res = self.client.patch(status_url, {"status": "confirmed"}, format="json")
        self.assertEqual(status_res.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")

        # 4. Cancel order and verify stock restored
        self.assertEqual(self.variant.stock, 15)
        cancel_url = reverse("admin-orders-cancel", kwargs={"pk": order.id})
        cancel_res = self.client.patch(cancel_url)
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)
        self.variant.refresh_from_db()
        # Stock should increase from 15 to 16 since quantity is 1
        self.assertEqual(self.variant.stock, 16)
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")

    def test_reports_sales_revenue_orders_customers_products(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Test reports endpoints
        for r_name in ["sales", "revenue", "orders", "customers", "products"]:
            res = self.client.get(reverse(f"admin-reports-{r_name}"))
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertIn("value", res.data)

    def test_profile_retrieve_and_update(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Retrieve profile
        res = self.client.get(reverse("admin-profile"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["username"], "adminuser")

        # Update profile
        update_res = self.client.put(
            reverse("admin-profile"),
            {"username": "adminuser-updated", "email": "admin@example.com"},
            format="json"
        )
        self.assertEqual(update_res.status_code, status.HTTP_200_OK)
        self.assertEqual(update_res.data["username"], "adminuser-updated")

    def test_change_password(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Change password
        res = self.client.post(
            reverse("admin-change-password"),
            {
                "old_password": "adminpassword123",
                "new_password": "newpassword12345",
                "confirm_password": "newpassword12345"
            },
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_settings_retrieve_and_update(self):
        login_res = self.client.post(
            self.login_url,
            {"email": "admin@example.com", "password": "adminpassword123"},
            format="json"
        )
        token = login_res.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Retrieve settings
        res = self.client.get(reverse("admin-settings"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["language"], "en")

        # Update settings
        update_res = self.client.put(
            reverse("admin-settings"),
            {
                "language": "fr",
                "notificationsEnabled": False,
                "emailAlerts": True,
                "timezone": "UTC"
            },
            format="json"
        )
        self.assertEqual(update_res.status_code, status.HTTP_200_OK)
        self.assertEqual(update_res.data["language"], "fr")






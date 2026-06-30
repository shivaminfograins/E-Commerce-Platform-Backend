from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.products.models import Category, Product
from .models import WishlistItem

User = get_user_model()

class WishlistAPITests(APITestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(
            email="wishlistuser@gmail.com",
            username="wishlistuser",
            password="wishlistpassword123"
        )
        # Create category and product
        self.category = Category.objects.create(
            name="Electronics",
            slug="electronics"
        )
        self.product = Product.objects.create(
            category=self.category,
            name="Laptop",
            slug="laptop",
            description="High performance laptop"
        )
        
        # URLs
        self.list_create_url = reverse("wishlist-list-create")
        self.delete_url_name = "wishlist-delete"

    def test_unauthenticated_access(self):
        # GET wishlist
        response = self.client.get(self.list_create_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # POST wishlist
        response = self.client.post(self.list_create_url, {"product": self.product.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # DELETE wishlist
        delete_url = reverse(self.delete_url_name, kwargs={"product_id": self.product.id})
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wishlist_lifecycle(self):
        # Login user via JWT
        login_url = reverse("token_obtain_pair")
        login_response = self.client.post(login_url, {
            "email": "wishlistuser@gmail.com",
            "password": "wishlistpassword123"
        }, format="json")
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        access_token = login_response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        
        # 1. Get wishlist (should be empty initially)
        response = self.client.get(self.list_create_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)
        
        # 2. Add product to wishlist
        response = self.client.post(self.list_create_url, {"product": self.product.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["product"]["id"], self.product.id)
        self.assertTrue(WishlistItem.objects.filter(user=self.user, product=self.product).exists())
        
        # 3. Get wishlist again (should contain 1 item)
        response = self.client.get(self.list_create_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["product"]["id"], self.product.id)
        
        # 4. Attempt to add the same product again (should fail validation)
        response = self.client.post(self.list_create_url, {"product": self.product.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # 5. Delete product from wishlist
        delete_url = reverse(self.delete_url_name, kwargs={"product_id": self.product.id})
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(WishlistItem.objects.filter(user=self.user, product=self.product).exists())
        
        # 6. Retrieve wishlist (should be empty again)
        response = self.client.get(self.list_create_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

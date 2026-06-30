from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase
from apps.accounts.models import Address, Profile

User = get_user_model()


class RegisterAPITests(APITestCase):
    def setUp(self):
        self.register_url = reverse("register")

    def test_successful_registration(self):
        data = {
            "email": "testuser@gmail.com",
            "username": "testuser",
            "password": "strongpassword123",
        }
        response = self.client.post(self.register_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["message"], "User registered successfully")
        self.assertTrue(User.objects.filter(email="testuser@gmail.com").exists())

    def test_registration_missing_fields(self):
        data = {
            "email": "testuser@gmail.com",
        }
        response = self.client.post(self.register_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProfileAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="profileuser@gmail.com",
            username="profileuser",
            password="profilepassword123"
        )
        self.profile_url = reverse("profile")
        
        # Get JWT Token
        login_url = reverse("token_obtain_pair")
        login_data = {
            "email": "profileuser@gmail.com",
            "password": "profilepassword123"
        }
        response = self.client.post(login_url, login_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.token = response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_get_profile_authenticated(self):
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"], self.user.id)

    def test_get_profile_unauthenticated(self):
        self.client.credentials()  # Clear auth
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_update_profile(self):
        data = {
            "phone": "1234567890",
            "date_of_birth": "1995-05-15"
        }
        response = self.client.put(self.profile_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["phone"], "1234567890")
        
        # Verify database
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.phone, "1234567890")


class AddressAPITests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            email="user1@gmail.com",
            username="user1",
            password="password123"
        )
        self.user2 = User.objects.create_user(
            email="user2@gmail.com",
            username="user2",
            password="password123"
        )
        self.address1 = Address.objects.create(
            user=self.user1,
            full_name="User One",
            phone="9876543210",
            address_line_1="123 Street",
            city="Metropolis",
            state="NY",
            country="USA",
            postal_code="10001"
        )
        self.addresses_url = "/api/auth/addresses/"
        self.address_detail_url = f"/api/auth/addresses/{self.address1.id}/"
        
        # Authenticate as user1
        login_url = reverse("token_obtain_pair")
        response = self.client.post(login_url, {"email": "user1@gmail.com", "password": "password123"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    def test_list_addresses(self):
        response = self.client.get(self.addresses_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["full_name"], "User One")

    def test_create_address(self):
        data = {
            "full_name": "New Address",
            "phone": "5551234567",
            "address_line_1": "456 Avenue",
            "city=": "Gotham",
            "city": "Gotham",
            "state": "NJ",
            "country": "USA",
            "postal_code": "07001",
            "is_default": True
        }
        response = self.client.post(self.addresses_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["full_name"], "New Address")
        self.assertTrue(Address.objects.filter(full_name="New Address", user=self.user1).exists())

    def test_get_address_detail_owner(self):
        response = self.client.get(self.address_detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "User One")

    def test_get_address_detail_non_owner(self):
        # Authenticate as user2
        login_url = reverse("token_obtain_pair")
        response = self.client.post(login_url, {"email": "user2@gmail.com", "password": "password123"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        
        response = self.client.get(self.address_detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_address(self):
        data = {
            "full_name": "Updated Name",
            "phone": "9876543210",
            "address_line_1": "123 Street",
            "city": "Metropolis",
            "state": "NY",
            "country": "USA",
            "postal_code": "10001"
        }
        response = self.client.put(self.address_detail_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Updated Name")

    def test_delete_address(self):
        response = self.client.delete(self.address_detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Address.objects.filter(id=self.address1.id).exists())


class PasswordResetAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="resetuser@gmail.com",
            username="resetuser",
            password="resetpassword123"
        )
        self.forgot_url = reverse("forgot-password")
        self.reset_url = reverse("reset-password")

    def test_forgot_password_valid_email(self):
        mail.outbox = []
        response = self.client.post(self.forgot_url, {"email": "resetuser@gmail.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("reset_link", response.data)
        self.assertEqual(response.data["message"], "Password reset link generated.")
        
        # Verify that an email was sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Password Reset Request")
        self.assertIn(response.data["reset_link"], mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["resetuser@gmail.com"])

    def test_forgot_password_invalid_email(self):
        mail.outbox = []
        response = self.client.post(self.forgot_url, {"email": "nonexistent@gmail.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_password_success(self):
        # Generate token
        forgot_res = self.client.post(self.forgot_url, {"email": "resetuser@gmail.com"}, format="json")
        link = forgot_res.data["reset_link"]
        # Link structure: http://localhost:3000/reset-password/{uid}/{token}/
        parts = link.strip("/").split("/")
        uid = parts[-2]
        token = parts[-1]

        reset_data = {
            "uid": uid,
            "token": token,
            "password": "newpassword123",
            "confirm_password": "newpassword123",
        }
        response = self.client.post(self.reset_url, reset_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Password reset successful")

        # Verify password change
        self.assertTrue(self.client.login(username="resetuser@gmail.com", password="newpassword123"))

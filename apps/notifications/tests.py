from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from django.db import transaction

from .models import Notification
from .services import NotificationService

User = get_user_model()


class NotificationAPITests(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="cust1", email="cust1@example.com", password="Password123", role="customer")
        self.user2 = User.objects.create_user(username="cust2", email="cust2@example.com", password="Password123", role="customer")
        
        self.notif1 = NotificationService.create(
            user=self.user1,
            event_key="ORDER_PLACED:ORD-001",
            title="Order Placed",
            message="Order ORD-001 placed.",
            notification_type="ORDER",
            reference_type="ORDER",
            reference_id="ORD-001"
        )
        self.notif2 = NotificationService.create(
            user=self.user1,
            event_key="ORDER_SHIPPED:VO-001",
            title="Order Shipped",
            message="Vendor order VO-001 shipped.",
            notification_type="ORDER",
            reference_type="VENDOR_ORDER",
            reference_id="VO-001"
        )
        self.notif3 = NotificationService.create(
            user=self.user2,
            event_key="ORDER_PLACED:ORD-002",
            title="Order Placed User 2",
            message="Order ORD-002 placed.",
            notification_type="ORDER",
            reference_type="ORDER",
            reference_id="ORD-002"
        )

    def test_customer_list_notifications_user_scoped(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 2)
        titles = [n["title"] for n in results]
        self.assertIn("Order Placed", titles)
        self.assertNotIn("Order Placed User 2", titles)

    def test_customer_unread_count_user_scoped(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 2)

    def test_mark_single_notification_read_user_scoped(self):
        self.client.force_authenticate(user=self.user1)
        url = f"/api/notifications/{self.notif1.id}/read/"
        response = self.client.patch(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notif1.refresh_from_db()
        self.assertTrue(self.notif1.is_read)

        # Attempt mark user2 notification by user1 -> 404
        url_other = f"/api/notifications/{self.notif3.id}/read/"
        response_other = self.client.patch(url_other)
        self.assertEqual(response_other.status_code, status.HTTP_404_NOT_FOUND)

    def test_mark_all_notifications_read(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.post("/api/notifications/read-all/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(user=self.user1, is_read=False).count(), 0)
        self.assertEqual(Notification.objects.filter(user=self.user2, is_read=False).count(), 1)

    def test_event_key_idempotency(self):
        # Attempt to create duplicate event notification
        dup = NotificationService.create(
            user=self.user1,
            event_key="ORDER_PLACED:ORD-001",
            title="Order Placed Duplicate",
            message="Order ORD-001 duplicate.",
            notification_type="ORDER",
        )
        self.assertEqual(dup.id, self.notif1.id)
        self.assertEqual(Notification.objects.filter(user=self.user1, event_key="ORDER_PLACED:ORD-001").count(), 1)

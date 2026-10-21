import logging
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.accounts.models import Address, User
from apps.orders.models import Order, OrderItem, VendorOrder
from apps.products.models import Category, Product, ProductVariant
from apps.shipping.dataclasses import CourierOption, ServiceabilityResult, ShipmentCreateRequest, ShipmentItem
from apps.shipping.exceptions import (
    ShippingConfigurationError,
    ShippingProviderNotFoundError,
    ShippingValidationError,
    ShiprocketAPIError,
    ShiprocketAuthenticationError,
    ShiprocketServiceabilityError,
)
from apps.shipping.models import Shipment, ShipmentStatusHistory
from apps.shipping.providers import ShiprocketProvider, get_shipping_provider
from apps.shipping.services import ShippingService
from apps.vendors.models import Vendor, VendorAddress


class ShippingDomainModelTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer@example.com",
            email="customer@example.com",
            password="Password123!",
            first_name="Jane",
            last_name="Doe",
            role="CUSTOMER"
        )
        self.address = Address.objects.create(
            user=self.customer,
            full_name="Jane Doe",
            phone="9876543210",
            address_line_1="123 MG Road",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452001",
            country="India"
        )

        self.vendor_user = User.objects.create_user(
            username="vendor@example.com",
            email="vendor@example.com",
            password="Password123!",
            first_name="Acme",
            last_name="Vendor",
            role="VENDOR"
        )
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Acme Electronics",
            status="APPROVED"
        )

        self.vendor_address = VendorAddress.objects.create(
            vendor=self.vendor,
            address_type="WAREHOUSE",
            full_name="Warehouse Manager",
            phone="9123456789",
            address_line_1="Plot 45, Industrial Area",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452015",
            shiprocket_pickup_location_name="Acme_Warehouse_Indore"
        )

        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(
            category=self.category,
            name="Wireless Headphones",
            slug="wireless-headphones",
            description="High quality noise cancelling headphones"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="Black",
            sku="WH-BLK-001",
            price=Decimal("1999.00"),
            stock=50,
            weight_kg=Decimal("0.450"),
            length_cm=Decimal("15.00"),
            width_cm=Decimal("12.00"),
            height_cm=Decimal("8.00")
        )

        self.master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.RAZORPAY,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("1999.00"),
            total_amount=Decimal("1999.00")
        )
        self.vendor_order = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor,
            vendor_order_number="VORD-TEST-001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("1999.00"),
            total_amount=Decimal("1999.00")
        )
        self.order_item = OrderItem.objects.create(
            order=self.master_order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("1999.00")
        )

    def test_shipment_creation(self):
        shipment = Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name=Shipment.PROVIDER_SHIPROCKET,
            weight_kg=self.variant.weight_kg,
            length_cm=self.variant.length_cm,
            width_cm=self.variant.width_cm,
            height_cm=self.variant.height_cm,
            pickup_location_name=self.vendor_address.shiprocket_pickup_location_name
        )
        self.assertIsNotNone(shipment.pk)
        self.assertEqual(shipment.status, Shipment.PENDING)
        self.assertEqual(shipment.vendor_order, self.vendor_order)
        self.assertEqual(shipment.pickup_location_name, "Acme_Warehouse_Indore")

    def test_duplicate_shipment_rejected(self):
        Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name=Shipment.PROVIDER_SHIPROCKET
        )
        duplicate_shipment = Shipment(
            vendor_order=self.vendor_order,
            provider_name=Shipment.PROVIDER_SHIPROCKET
        )
        with self.assertRaises(ValidationError):
            duplicate_shipment.full_clean()

    def test_valid_status_transition_sequence(self):
        shipment = Shipment.objects.create(vendor_order=self.vendor_order)
        self.assertEqual(shipment.status, Shipment.PENDING)

        shipment.transition_to(Shipment.CREATED, remarks="Order created in provider system")
        self.assertEqual(shipment.status, Shipment.CREATED)

        shipment.transition_to(Shipment.COURIER_ASSIGNED, remarks="Courier assigned")
        self.assertEqual(shipment.status, Shipment.COURIER_ASSIGNED)

        shipment.transition_to(Shipment.AWB_ASSIGNED, remarks="AWB assigned")
        self.assertEqual(shipment.status, Shipment.AWB_ASSIGNED)

        shipment.transition_to(Shipment.PICKUP_REQUESTED, remarks="Pickup requested")
        self.assertEqual(shipment.status, Shipment.PICKUP_REQUESTED)

        shipment.transition_to(Shipment.PICKED_UP, remarks="Courier scanned package")
        self.assertEqual(shipment.status, Shipment.PICKED_UP)

        shipment.transition_to(Shipment.IN_TRANSIT, location="Indore Hub", remarks="Package in transit")
        self.assertEqual(shipment.status, Shipment.IN_TRANSIT)

        shipment.transition_to(Shipment.OUT_FOR_DELIVERY, location="Destination Hub", remarks="Out for delivery")
        self.assertEqual(shipment.status, Shipment.OUT_FOR_DELIVERY)

        shipment.transition_to(Shipment.DELIVERED, location="Customer Doorstep", remarks="Delivered successfully")
        self.assertEqual(shipment.status, Shipment.DELIVERED)

        self.assertEqual(shipment.status_history.count(), 8)

    def test_invalid_status_transition_rejected(self):
        shipment = Shipment.objects.create(vendor_order=self.vendor_order)
        self.assertEqual(shipment.status, Shipment.PENDING)

        with self.assertRaises(ValidationError):
            shipment.transition_to(Shipment.DELIVERED)

        self.assertEqual(shipment.status, Shipment.PENDING)

    def test_invalid_package_dimensions_rejected(self):
        shipment = Shipment(
            vendor_order=self.vendor_order,
            weight_kg=Decimal("-1.000")
        )
        with self.assertRaises(ValidationError):
            shipment.full_clean()

        variant = ProductVariant(
            product=self.product,
            name="Invalid",
            sku="INV-001",
            price=Decimal("100.00"),
            weight_kg=Decimal("0.000")
        )
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_existing_order_behavior_unchanged(self):
        self.assertEqual(self.master_order.vendor_orders.count(), 1)
        self.assertEqual(self.vendor_order.items.count(), 1)
        self.assertEqual(self.master_order.status, Order.CONFIRMED)

    def test_product_variant_dimensions(self):
        self.assertEqual(self.variant.weight_kg, Decimal("0.450"))
        self.assertEqual(self.variant.length_cm, Decimal("15.00"))
        self.assertEqual(self.variant.width_cm, Decimal("12.00"))
        self.assertEqual(self.variant.height_cm, Decimal("8.00"))

    def test_vendor_address_shiprocket_pickup_location(self):
        self.assertEqual(self.vendor_address.shiprocket_pickup_location_name, "Acme_Warehouse_Indore")


    def test_concurrency_attempt_lock_raises_409(self):
        """
        Verifies that an active creation attempt lock raises ShippingConcurrencyError,
        which VendorOrderCreateShipmentView transforms into HTTP 409 Conflict.
        """
        import time, uuid
        shipment = Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name="shiprocket",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=Shipment.PENDING,
            metadata={
                "creation_attempt_id": str(uuid.uuid4()),
                "creation_started_at": time.time(),
            },
        )
        from django.urls import reverse
        url = reverse("vendor-order-create-shipment", kwargs={"vendor_order_number": self.vendor_order.vendor_order_number})
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.vendor_user)
        response = client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("currently in progress", response.data["detail"])

    def test_ownership_aware_lock_cleanup(self):
        """
        Verifies that an older/delayed request does not clear a newer request's attempt lock.
        """
        import time, uuid
        shipment = Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name="shiprocket",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=Shipment.PENDING,
            metadata={
                "creation_attempt_id": "newer-attempt-uuid",
                "creation_started_at": time.time(),
            },
        )
        attempt_id = "older-attempt-uuid"
        from django.db import transaction
        with transaction.atomic():
            current_shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
            meta = current_shipment.metadata or {}
            if meta.get("creation_attempt_id") == attempt_id:
                meta.pop("creation_attempt_id", None)
                meta.pop("creation_started_at", None)
                current_shipment.metadata = meta
                current_shipment.save(update_fields=["metadata", "updated_at"])

        shipment.refresh_from_db()
        self.assertEqual(shipment.metadata.get("creation_attempt_id"), "newer-attempt-uuid")

    def test_stale_lock_recovery(self):
        """
        Verifies that an expired lock (started > 60s ago) allows stale lock recovery.
        """
        import time, uuid
        stale_time = time.time() - 120
        shipment = Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name="shiprocket",
            weight_kg=Decimal("1.000"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00"),
            status=Shipment.PENDING,
            metadata={
                "creation_attempt_id": str(uuid.uuid4()),
                "creation_started_at": stale_time,
            },
        )
        with patch("apps.shipping.providers.shiprocket.ShiprocketProvider.create_order") as mock_create, \
             patch("apps.shipping.providers.shiprocket.ShiprocketProvider.assign_awb") as mock_awb:
            mock_create.return_value = ShipmentCreateResult(
                provider_order_id="SR_REC_1001",
                provider_shipment_id="SR_REC_SHIP_1001",
                status="NEW",
            )
            mock_awb.return_value = AWBAssignmentResult(
                awb_code="AWB_REC_9999",
                courier_company_id="10",
                courier_name="Delhivery",
            )
            res_shipment, is_new = ShippingService.create_shipment(self.vendor_order)
            self.assertFalse(is_new)
            self.assertEqual(res_shipment.awb_code, "AWB_REC_9999")


class ShiprocketProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.mock_session = MagicMock(spec=requests.Session)

    def tearDown(self):
        cache.clear()

    @override_settings(
        SHIPROCKET_EMAIL="test@example.com",
        SHIPROCKET_PASSWORD="secretpassword123"
    )
    def test_successful_authentication(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "token": "TEST_MOCKED_TOKEN_12345",
            "first_name": "Test",
            "last_name": "User"
        }
        self.mock_session.request.return_value = mock_response

        with self.assertLogs("apps.shipping.providers.shiprocket", level="INFO") as cm:
            provider = ShiprocketProvider(session=self.mock_session)
            token = provider.authenticate()

        self.assertEqual(token, "TEST_MOCKED_TOKEN_12345")
        cached_token = cache.get("shipping:shiprocket:auth_token")
        self.assertEqual(cached_token, "TEST_MOCKED_TOKEN_12345")

        log_output = " ".join(cm.output)
        self.assertNotIn("secretpassword123", log_output)
        self.assertNotIn("TEST_MOCKED_TOKEN_12345", log_output)

    @override_settings(
        SHIPROCKET_EMAIL="test@example.com",
        SHIPROCKET_PASSWORD="secretpassword123"
    )
    def test_cached_token_returned_without_http_call(self):
        cache.set("shipping:shiprocket:auth_token", "PRE_CACHED_TOKEN_999", timeout=86400)

        provider = ShiprocketProvider(session=self.mock_session)
        token = provider.authenticate()

        self.assertEqual(token, "PRE_CACHED_TOKEN_999")
        self.mock_session.request.assert_not_called()

    @override_settings(
        SHIPROCKET_EMAIL="test@example.com",
        SHIPROCKET_PASSWORD="wrongpassword"
    )
    def test_authentication_failure_raises_exception(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.ok = False
        mock_response.json.return_value = {
            "message": "The username or password provided is incorrect."
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAuthenticationError) as cm:
            provider.authenticate()

        self.assertEqual(cm.exception.status_code, 401)
        self.assertIn("Shiprocket authentication failed", str(cm.exception))

    @override_settings(
        SHIPROCKET_EMAIL="test@example.com",
        SHIPROCKET_PASSWORD="secretpassword123"
    )
    def test_request_timeout_raises_api_error(self):
        self.mock_session.request.side_effect = requests.exceptions.Timeout("Connection timed out")

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError) as cm:
            provider.authenticate()

        self.assertIn("timed out", str(cm.exception))

    @override_settings(
        SHIPROCKET_EMAIL="test@example.com",
        SHIPROCKET_PASSWORD="secretpassword123"
    )
    def test_invalid_json_response_raises_api_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError) as cm:
            provider.authenticate()

        self.assertIn("Invalid JSON", str(cm.exception))

    @override_settings(
        SHIPROCKET_EMAIL="",
        SHIPROCKET_PASSWORD=""
    )
    def test_missing_credentials_raises_configuration_error(self):
        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShippingConfigurationError) as cm:
            provider.authenticate()

        self.assertEqual(str(cm.exception), "Shiprocket credentials are not configured.")
        self.mock_session.request.assert_not_called()

    def test_provider_factory_returns_shiprocket_provider(self):
        provider = get_shipping_provider("shiprocket")
        self.assertIsInstance(provider, ShiprocketProvider)

    def test_provider_factory_unknown_provider_raises_error(self):
        with self.assertRaises(ShippingProviderNotFoundError) as cm:
            get_shipping_provider("unknown_provider")

        self.assertIn("Unsupported shipping provider: 'unknown_provider'", str(cm.exception))

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_create_order_payload_building(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "order_id": 10001,
            "shipment_id": 20002,
            "status": "NEW",
            "status_code": 1
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        req = ShipmentCreateRequest(
            order_id="VORD-123",
            order_date="2026-10-05 12:00",
            pickup_location="Warehouse",
            billing_customer_name="John Doe",
            billing_address="123 Main St",
            billing_city="Indore",
            billing_pincode="452001",
            billing_state="MP",
            billing_country="India",
            billing_phone="9876543210",
        )

        res = provider.create_order(req)
        self.assertEqual(res.provider_order_id, "10001")
        self.assertEqual(res.provider_shipment_id, "20002")

        _, kwargs = self.mock_session.request.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["shipping_last_name"], "")
        self.assertEqual(payload["billing_customer_name"], "John Doe")



class ShiprocketServiceabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.mock_session = MagicMock(spec=requests.Session)

        self.customer = User.objects.create_user(
            username="cust3@example.com",
            email="cust3@example.com",
            password="Password123!",
            role="CUSTOMER"
        )
        self.delivery_address = Address.objects.create(
            user=self.customer,
            full_name="John Customer",
            phone="9876543210",
            address_line_1="100 MG Road",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452001",
            country="India"
        )

        self.vendor_user = User.objects.create_user(
            username="vend3@example.com",
            email="vend3@example.com",
            password="Password123!",
            role="VENDOR"
        )
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Tech Store",
            status="APPROVED"
        )
        self.vendor_address = VendorAddress.objects.create(
            vendor=self.vendor,
            address_type="WAREHOUSE",
            full_name="Warehouse Mgr",
            phone="9123456789",
            address_line_1="Industrial Area",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452015",
            shiprocket_pickup_location_name="Tech_Store_Warehouse"
        )

        self.category = Category.objects.create(name="Gadgets", slug="gadgets")
        self.product = Product.objects.create(category=self.category, name="Smart Watch", slug="smart-watch")

        self.variant1 = ProductVariant.objects.create(
            product=self.product,
            name="Black Watch",
            sku="SW-BLK",
            price=Decimal("2000.00"),
            stock=10,
            weight_kg=Decimal("0.450"),
            length_cm=Decimal("15.00"),
            width_cm=Decimal("12.00"),
            height_cm=Decimal("8.00")
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product,
            name="Strap Accessory",
            sku="SW-STRAP",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.300"),
            length_cm=Decimal("20.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("5.00")
        )

        self.order = Order.objects.create(
            user=self.customer,
            address=self.delivery_address,
            snapshot_postal_code=self.delivery_address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.RAZORPAY,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("4500.00"),
            total_amount=Decimal("4500.00")
        )
        self.vendor_order = VendorOrder.objects.create(
            master_order=self.order,
            vendor=self.vendor,
            vendor_order_number="VORD-P3-001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("4500.00"),
            total_amount=Decimal("4500.00")
        )
        self.item1 = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant1,
            product_name=self.product.name,
            variant_name=self.variant1.name,
            sku=self.variant1.sku,
            price=self.variant1.price,
            quantity=2,
            total=Decimal("4000.00")
        )

    def tearDown(self):
        cache.clear()

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_successful_serviceability_response(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": 200,
            "data": {
                "available_courier_companies": [
                    {
                        "courier_company_id": 1,
                        "courier_name": "Delhivery Surface",
                        "rate": 65.0,
                        "estimated_delivery_days": "3",
                        "etd": "Sep 12, 2026",
                        "cod": 1,
                        "rating": 4.5,
                        "min_weight": 0.5
                    },
                    {
                        "courier_company_id": 2,
                        "courier_name": "BlueDart Express",
                        "rate": 120.0,
                        "estimated_delivery_days": "1",
                        "etd": "Sep 10, 2026",
                        "cod": 1,
                        "rating": 4.8,
                        "min_weight": 0.5
                    }
                ],
                "recommended_courier_company_id": 1,
                "is_serviceable": True
            }
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        result = provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.450"),
            length=Decimal("15.00"),
            width=Decimal("12.00"),
            height=Decimal("8.00")
        )

        self.assertTrue(result.is_serviceable)
        self.assertEqual(len(result.courier_options), 2)
        self.assertEqual(result.recommended_courier.courier_id, "1")
        self.assertEqual(result.cheapest_courier.courier_id, "1")
        self.assertEqual(result.fastest_courier.courier_id, "2")

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_unserviceable_route_response(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": 200,
            "data": {
                "available_courier_companies": [],
                "is_serviceable": False
            }
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        result = provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="999999",
            weight=Decimal("0.450")
        )

        self.assertFalse(result.is_serviceable)
        self.assertEqual(len(result.courier_options), 0)
        self.assertIsNone(result.recommended_courier)
        self.assertIsNone(result.cheapest_courier)
        self.assertIsNone(result.fastest_courier)

    def test_invalid_pickup_pincode_validation(self):
        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShippingValidationError) as cm:
            provider.check_serviceability(
                origin_pincode="123",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )
        self.assertIn("Origin pincode", str(cm.exception))

    def test_invalid_delivery_pincode_validation(self):
        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShippingValidationError) as cm:
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="ABCDEF",
                weight=Decimal("0.450")
            )
        self.assertIn("Destination pincode", str(cm.exception))

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_cod_serviceability_request_param(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {"status": 200, "data": {"available_courier_companies": [], "is_serviceable": True}}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.450"),
            is_cod=True
        )

        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["params"]["cod"], 1)

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_prepaid_serviceability_request_param(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {"status": 200, "data": {"available_courier_companies": [], "is_serviceable": True}}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.450"),
            is_cod=False
        )

        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["params"]["cod"], 0)

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_auth_header_included_in_serviceability_request(self):
        cache.set("shipping:shiprocket:auth_token", "BEARER_TOKEN_ABC", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {"status": 200, "data": {"available_courier_companies": [], "is_serviceable": True}}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.450")
        )

        _, kwargs = self.mock_session.request.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer BEARER_TOKEN_ABC")

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_serviceability_api_timeout(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        self.mock_session.request.side_effect = requests.exceptions.Timeout("Timed out")

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_serviceability_api_connection_failure(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        self.mock_session.request.side_effect = requests.exceptions.ConnectionError("Connection refused")

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_serviceability_api_401_auth_error(self):
        cache.set("shipping:shiprocket:auth_token", "EXPIRED_TOKEN", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.ok = False
        mock_response.json.return_value = {"message": "Unauthenticated."}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAuthenticationError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_serviceability_api_5xx_server_error(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.json.return_value = {"message": "Internal Server Error"}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_serviceability_api_malformed_json(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON response")
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketAPIError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    def test_missing_package_weight_validation(self):
        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShippingValidationError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.000")
            )

        with self.assertRaises(ShippingValidationError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("-0.500")
            )

    def test_invalid_package_dimensions_validation(self):
        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShippingValidationError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450"),
                length=Decimal("0.00")
            )

        with self.assertRaises(ShippingValidationError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450"),
                width=Decimal("-5.00")
            )

    def test_multi_item_vendor_order_package_calculation(self):
        OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant2,
            product_name=self.product.name,
            variant_name=self.variant2.name,
            sku=self.variant2.sku,
            price=self.variant2.price,
            quantity=1,
            total=Decimal("500.00")
        )

        (
            weight,
            length,
            width,
            height,
            declared_val,
            origin_pin,
            dest_pin
        ) = ShippingService.calculate_vendor_order_package(self.vendor_order)

        self.assertEqual(weight, Decimal("1.200"))
        self.assertEqual(length, Decimal("20.00"))
        self.assertEqual(width, Decimal("12.00"))
        self.assertEqual(height, Decimal("21.00"))
        self.assertEqual(declared_val, Decimal("4500.00"))
        self.assertEqual(origin_pin, "452015")
        self.assertEqual(dest_pin, "452001")

    def test_provider_independent_response_mapping(self):
        option = CourierOption(
            courier_id="101",
            courier_name="Surface Express",
            shipping_charge=Decimal("75.50"),
            estimated_delivery_days="2",
            estimated_delivery_date="Sep 11, 2026",
            is_cod_available=True,
            rating=4.2,
            min_weight=Decimal("0.500")
        )
        result = ServiceabilityResult(
            is_serviceable=True,
            origin_pincode="452015",
            destination_pincode="452001",
            total_weight_kg=Decimal("1.000"),
            courier_options=[option],
            recommended_courier=option,
            cheapest_courier=option,
            fastest_courier=option
        )
        self.assertTrue(result.is_serviceable)
        self.assertEqual(result.courier_options[0].courier_name, "Surface Express")
        self.assertEqual(result.courier_options[0].shipping_charge, Decimal("75.50"))

    def test_cache_key_changes_when_dimensions_change(self):
        key1 = ShippingService.generate_cache_key(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.500"),
            length=Decimal("15.00"),
            width=Decimal("12.00"),
            height=Decimal("8.00")
        )
        key2 = ShippingService.generate_cache_key(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.500"),
            length=Decimal("20.00"),
            width=Decimal("12.00"),
            height=Decimal("8.00")
        )
        self.assertNotEqual(key1, key2)
        self.assertTrue(key1.startswith("shipping:serviceability:"))
        self.assertTrue(key2.startswith("shipping:serviceability:"))

    def test_cache_key_changes_when_declared_value_changes(self):
        key1 = ShippingService.generate_cache_key(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.500"),
            declared_value=Decimal("1000.00")
        )
        key2 = ShippingService.generate_cache_key(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.500"),
            declared_value=Decimal("2000.00")
        )
        self.assertNotEqual(key1, key2)

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_malformed_courier_object_raises_serviceability_error(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": 200,
            "data": {
                "available_courier_companies": ["invalid_string_element"],
                "is_serviceable": True
            }
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketServiceabilityError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_missing_available_couriers_key_raises_serviceability_error(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": 200,
            "data": {
                "is_serviceable": True
            }
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with self.assertRaises(ShiprocketServiceabilityError):
            provider.check_serviceability(
                origin_pincode="452015",
                destination_pincode="452001",
                weight=Decimal("0.450")
            )

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_http_client_params_passed_correctly(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {"status": 200, "data": {"available_courier_companies": [], "is_serviceable": True}}
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        provider.check_serviceability(
            origin_pincode="452015",
            destination_pincode="452001",
            weight=Decimal("0.450"),
            length=Decimal("15.00"),
            width=Decimal("12.00"),
            height=Decimal("8.00"),
            declared_value=Decimal("1999.00")
        )

        _, kwargs = self.mock_session.request.call_args
        params = kwargs["params"]
        self.assertEqual(params["pickup_postcode"], "452015")
        self.assertEqual(params["delivery_postcode"], "452001")
        self.assertEqual(params["weight"], "0.450")
        self.assertEqual(params["cod"], 0)
        self.assertEqual(params["length"], "15.00")
        self.assertEqual(params["breadth"], "12.00")
        self.assertEqual(params["height"], "8.00")
        self.assertEqual(params["declared_value"], "1999.00")

    @override_settings(SHIPROCKET_EMAIL="test@example.com", SHIPROCKET_PASSWORD="password123")
    def test_phase2_tests_regression_pass(self):
        cache.set("shipping:shiprocket:auth_token", "VALID_TOKEN_123", timeout=3600)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": 200,
            "data": {
                "available_courier_companies": [
                    {
                        "courier_company_id": 1,
                        "courier_name": "Delhivery Surface",
                        "rate": 65.0,
                        "estimated_delivery_days": "3",
                        "etd": "Sep 12, 2026",
                        "cod": 1,
                        "rating": 4.5,
                        "min_weight": 0.5
                    }
                ],
                "is_serviceable": True
            }
        }
        self.mock_session.request.return_value = mock_response

        provider = ShiprocketProvider(session=self.mock_session)
        with patch("apps.shipping.services.get_shipping_provider", return_value=provider):
            res1 = ShippingService.check_serviceability_for_vendor_order(
                vendor_order=self.vendor_order,
                is_cod=False,
                provider_name="shiprocket"
            )
            self.assertTrue(res1.is_serviceable)
            self.assertEqual(len(res1.courier_options), 1)

            call_count = self.mock_session.request.call_count
            res2 = ShippingService.check_serviceability_for_vendor_order(
                vendor_order=self.vendor_order,
                is_cod=False,
                provider_name="shiprocket"
            )
            self.assertEqual(self.mock_session.request.call_count, call_count)
            self.assertTrue(res2.is_serviceable)

class ShippingPackageValidationTests(TestCase):
    def test_missing_origin_pincode_raises_validation_error(self):
        if hasattr(self.vendor, "addresses"):
            self.vendor.addresses.all().delete()
        if hasattr(self.vendor, "shipping_addresses"):
            self.vendor.shipping_addresses.all().delete()
        if hasattr(self.vendor, "profile") and self.vendor.profile:
            self.vendor.profile.pincode = ""
            self.vendor.profile.save()

        OrderItem.objects.get_or_create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("500.00")
        )

        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("pincode", str(cm.exception).lower())

    def test_missing_destination_pincode_raises_validation_error(self):
        self.order.snapshot_postal_code = ""
        self.order.shipping_address = None
        self.order.save()

        OrderItem.objects.get_or_create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("500.00")
        )

        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("destination", str(cm.exception).lower())

    def setUp(self):
        self.customer = User.objects.create_user(
            username="cust_val@example.com",
            email="cust_val@example.com",
            password="Password123!",
            role="CUSTOMER"
        )
        self.address = Address.objects.create(
            user=self.customer,
            full_name="Jane Customer",
            phone="9876543210",
            address_line_1="100 MG Road",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452001",
            country="India"
        )
        self.vendor_user = User.objects.create_user(
            username="vend_val@example.com",
            email="vend_val@example.com",
            password="Password123!",
            role="VENDOR"
        )
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Validation Store",
            status="APPROVED"
        )
        self.vendor_address = VendorAddress.objects.create(
            vendor=self.vendor,
            address_type="WAREHOUSE",
            full_name="Warehouse Mgr",
            phone="9123456789",
            address_line_1="Industrial Area",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452015",
            shiprocket_pickup_location_name="Val_Store_Warehouse"
        )
        self.category = Category.objects.create(name="Validation Category", slug="val-cat")
        self.product = Product.objects.create(category=self.category, name="Val Product", slug="val-product")
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="Val Variant",
            sku="VAL-VAR-001",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )

        self.order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_postal_code=self.address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.RAZORPAY,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00")
        )
        self.vendor_order = VendorOrder.objects.create(
            master_order=self.order,
            vendor=self.vendor,
            vendor_order_number="VORD-VAL-001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("1000.00"),
            total_amount=Decimal("1000.00")
        )

    def test_missing_weight_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="No Weight",
            sku="SKUNO-WEIGHT",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.weight_kg = None
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKUNO-WEIGHT", str(cm.exception))
        self.assertIn("Shipping weight is missing or invalid", str(cm.exception))

    def test_zero_weight_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="Zero Weight",
            sku="SKU-ZERO-WEIGHT",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.weight_kg = Decimal("0.000")
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKU-ZERO-WEIGHT", str(cm.exception))
        self.assertIn("Shipping weight is missing or invalid", str(cm.exception))

    def test_negative_weight_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="Neg Weight",
            sku="SKU-NEG-WEIGHT",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.weight_kg = Decimal("-0.500")
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKU-NEG-WEIGHT", str(cm.exception))
        self.assertIn("Shipping weight is missing or invalid", str(cm.exception))

    def test_missing_length_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="No Length",
            sku="SKUNO-LENGTH",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.length_cm = None
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKUNO-LENGTH", str(cm.exception))
        self.assertIn("Shipping length is missing or invalid", str(cm.exception))

    def test_missing_width_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="No Width",
            sku="SKU-NO-WIDTH",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.width_cm = None
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKU-NO-WIDTH", str(cm.exception))
        self.assertIn("Shipping width is missing or invalid", str(cm.exception))

    def test_missing_height_raises_validation_error(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="No Height",
            sku="SKU-NO-HEIGHT",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )
        variant.height_cm = None
        item = OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=1,
            price=variant.price
        )
        item.variant = variant
        self.vendor_order._prefetched_objects_cache = {'items': [item]}
        with self.assertRaises(ShippingValidationError) as cm:
            ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertIn("SKU-NO-HEIGHT", str(cm.exception))
        self.assertIn("Shipping height is missing or invalid", str(cm.exception))

    def test_valid_shipping_data_calculates_successfully(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            name="Valid Item",
            sku="SKU-VALID-01",
            price=Decimal("500.00"),
            stock=10,
            weight_kg=Decimal("0.600"),
            length_cm=Decimal("25.00"),
            width_cm=Decimal("20.00"),
            height_cm=Decimal("5.00")
        )
        OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=variant,
            quantity=2,
            price=variant.price
        )
        weight, length, width, height, price, origin, dest = ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertEqual(weight, Decimal("1.200"))
        self.assertEqual(length, Decimal("25.00"))
        self.assertEqual(width, Decimal("20.00"))
        self.assertEqual(height, Decimal("10.00"))
        self.assertEqual(price, Decimal("1000.00"))
        self.assertEqual(origin, "452015")
        self.assertEqual(dest, "452001")

    def test_multi_item_vendor_order_package_calculation(self):
        v1 = ProductVariant.objects.create(
            product=self.product,
            name="Item 1",
            sku="SKU-MULTI-1",
            price=Decimal("300.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("15.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("4.00")
        )
        v2 = ProductVariant.objects.create(
            product=self.product,
            name="Item 2",
            sku="SKU-MULTI-2",
            price=Decimal("700.00"),
            stock=10,
            weight_kg=Decimal("0.800"),
            length_cm=Decimal("30.00"),
            width_cm=Decimal("25.00"),
            height_cm=Decimal("6.00")
        )
        OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=v1,
            quantity=1,
            price=v1.price
        )
        OrderItem.objects.create(
            order=self.order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=v2,
            quantity=2,
            price=v2.price
        )
        weight, length, width, height, price, origin, dest = ShippingService.calculate_vendor_order_package(self.vendor_order)
        self.assertEqual(weight, Decimal("2.100"))
        self.assertEqual(length, Decimal("30.00"))
        self.assertEqual(width, Decimal("25.00"))
        self.assertEqual(height, Decimal("16.00"))
        self.assertEqual(price, Decimal("1700.00"))



from unittest.mock import patch, MagicMock
from rest_framework import status
from rest_framework.test import APIClient
from apps.shipping.dataclasses import ShipmentCreateResult, AWBAssignmentResult
from apps.shipping.exceptions import ShiprocketAPIError, ShippingValidationError


class Phase4ShipmentCreationTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer_p4@example.com",
            email="customer_p4@example.com",
            password="Password123!",
            first_name="Jane",
            last_name="Doe",
            role="CUSTOMER"
        )
        self.address = Address.objects.create(
            user=self.customer,
            full_name="Jane Customer",
            phone="9876543210",
            address_line_1="100 Park Street",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452001",
            country="India"
        )

        self.vendor_user = User.objects.create_user(
            username="vendor_p4@example.com",
            email="vendor_p4@example.com",
            password="Password123!",
            first_name="VendorP4",
            last_name="Owner",
            role="VENDOR"
        )
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="P4 Electronics",
            business_name="P4 Business Inc",
            business_email="vendor_p4@example.com",
            business_phone="9988776655",
            status="APPROVED"
        )
        self.vendor_address = VendorAddress.objects.create(
            vendor=self.vendor,
            address_type="WAREHOUSE",
            full_name="P4 Manager",
            phone="9123456789",
            address_line_1="Building 12, Tech Park",
            city="Indore",
            state="Madhya Pradesh",
            postal_code="452015",
            shiprocket_pickup_location_name="P4_Warehouse_Indore"
        )

        self.category = Category.objects.create(name="Gadgets", slug="gadgets")
        self.product = Product.objects.create(
            category=self.category,
            name="Smart Watch",
            slug="smart-watch",
            description="Fitness tracker watch"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="Silver",
            sku="SW-SLV-001",
            price=Decimal("2999.00"),
            stock=20,
            weight_kg=Decimal("0.300"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("8.00"),
            height_cm=Decimal("5.00")
        )

        self.master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.RAZORPAY,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("2999.00"),
            total_amount=Decimal("2999.00")
        )

        self.vendor_order = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor,
            vendor_order_number="VORD-P4-TEST-001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("2999.00"),
            total_amount=Decimal("2999.00")
        )

        self.order_item = OrderItem.objects.create(
            order=self.master_order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("2999.00")
        )

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_create_shipment_success(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(
            provider_order_id="SR-ORD-10001",
            provider_shipment_id="SR-SHIP-20001",
            status="NEW",
            raw_response={"order_id": "SR-ORD-10001"}
        )
        mock_assign.return_value = AWBAssignmentResult(
            awb_code="AWB-DELHIVERY-999",
            courier_company_id="10",
            courier_name="Delhivery",
            raw_response={"awb_code": "AWB-DELHIVERY-999"}
        )

        shipment, is_new = ShippingService.create_shipment(self.vendor_order, courier_id="10")

        self.assertTrue(is_new)
        self.assertEqual(shipment.provider_order_id, "SR-ORD-10001")
        self.assertEqual(shipment.provider_shipment_id, "SR-SHIP-20001")
        self.assertEqual(shipment.awb_code, "AWB-DELHIVERY-999")
        self.assertEqual(shipment.courier_name, "Delhivery")
        self.assertEqual(shipment.courier_id, "10")
        self.assertEqual(shipment.status, Shipment.AWB_ASSIGNED)
        self.assertGreaterEqual(shipment.status_history.count(), 3)

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_package_snapshot_preservation(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-O1", provider_shipment_id="SR-S1")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB1", courier_company_id="1", courier_name="X")

        shipment, _ = ShippingService.create_shipment(self.vendor_order)
        self.assertEqual(shipment.weight_kg, Decimal("0.300"))
        self.assertEqual(shipment.length_cm, Decimal("10.00"))

        # Mutate ProductVariant in DB
        self.variant.weight_kg = Decimal("99.999")
        self.variant.save()

        # Reload shipment from DB and verify snapshot remains unchanged
        shipment.refresh_from_db()
        self.assertEqual(shipment.weight_kg, Decimal("0.300"))

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_immutable_destination_address_used(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-O2", provider_shipment_id="SR-S2")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB2", courier_company_id="1", courier_name="Y")

        ShippingService.create_shipment(self.vendor_order)

        self.assertTrue(mock_create.called)
        req = mock_create.call_args[0][0]
        self.assertEqual(req.billing_address, self.master_order.snapshot_address_line_1)
        self.assertEqual(req.billing_pincode, self.master_order.snapshot_postal_code)

        # Update customer address
        self.address.address_line_1 = "999 Changed Road"
        self.address.postal_code = "110001"
        self.address.save()

        # Verify master order snapshot is still used
        self.assertEqual(req.billing_address, "100 Park Street")
        self.assertEqual(req.billing_pincode, "452001")

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_cod_order_creation(self, mock_create, mock_assign):
        self.master_order.payment_method = Order.COD
        self.master_order.payment_status = Order.PAYMENT_PENDING
        self.master_order.save()

        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-COD-1", provider_shipment_id="SR-COD-S1")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-COD", courier_company_id="1", courier_name="CodExpress")

        shipment, _ = ShippingService.create_shipment(self.vendor_order)
        req = mock_create.call_args[0][0]
        self.assertEqual(req.payment_method, "COD")

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_duplicate_shipment_protection(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-DUP-1", provider_shipment_id="SR-DUP-S1")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-DUP", courier_company_id="1", courier_name="DupExpress")

        s1, created1 = ShippingService.create_shipment(self.vendor_order)
        self.assertTrue(created1)

        s2, created2 = ShippingService.create_shipment(self.vendor_order)
        self.assertFalse(created2)
        self.assertEqual(s1.pk, s2.pk)
        self.assertEqual(mock_create.call_count, 1)

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_partial_failure_retry_behavior(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-PARTIAL-1", provider_shipment_id="SR-PARTIAL-S1")
        mock_assign.side_effect = ShiprocketAPIError("AWB server down")

        with self.assertRaises(ShiprocketAPIError):
            ShippingService.create_shipment(self.vendor_order)

        shipment = Shipment.objects.get(vendor_order=self.vendor_order)
        self.assertEqual(shipment.provider_order_id, "SR-PARTIAL-1")
        self.assertEqual(shipment.provider_shipment_id, "SR-PARTIAL-S1")
        self.assertEqual(shipment.awb_code, "")
        self.assertEqual(shipment.status, Shipment.FAILED)

        # Retry with fixed AWB response
        mock_assign.side_effect = None
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-RETRY-OK", courier_company_id="5", courier_name="RetryCourier")

        s_retry, created = ShippingService.create_shipment(self.vendor_order)
        self.assertFalse(created)
        self.assertEqual(mock_create.call_count, 1) # NOT called again
        self.assertEqual(s_retry.awb_code, "AWB-RETRY-OK")
        self.assertEqual(s_retry.status, Shipment.AWB_ASSIGNED)

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_invalid_courier_id_retry(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-INV-1", provider_shipment_id="SR-INV-S1")
        mock_assign.side_effect = ShiprocketAPIError("Invalid courier company ID")

        with self.assertRaises(ShiprocketAPIError):
            ShippingService.create_shipment(self.vendor_order, courier_id="INVALID_999")

        shipment = Shipment.objects.get(vendor_order=self.vendor_order)
        self.assertEqual(shipment.status, Shipment.FAILED)
        self.assertEqual(shipment.provider_order_id, "SR-INV-1")

        # Retry with valid courier ID
        mock_assign.side_effect = None
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-VALID-10", courier_company_id="10", courier_name="ValidCourier")

        s_retry, _ = ShippingService.create_shipment(self.vendor_order, courier_id="10")
        self.assertEqual(s_retry.awb_code, "AWB-VALID-10")
        self.assertEqual(s_retry.status, Shipment.AWB_ASSIGNED)


class Phase4ShipmentAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.customer = User.objects.create_user(
            username="cust_api@example.com",
            email="cust_api@example.com",
            password="Password123!",
            role="CUSTOMER"
        )
        self.address = Address.objects.create(
            user=self.customer,
            full_name="API Customer",
            phone="9876543210",
            address_line_1="100 Main St",
            city="Indore",
            state="MP",
            postal_code="452001",
            country="India"
        )

        self.vendor_a_user = User.objects.create_user(
            username="vend_a@example.com",
            email="vend_a@example.com",
            password="Password123!",
            role="VENDOR"
        )
        self.vendor_a = Vendor.objects.create(
            user=self.vendor_a_user,
            store_name="Vendor A Store",
            business_name="Vendor A Inc",
            business_email="vend_a@example.com",
            business_phone="9111111111",
            status="APPROVED"
        )
        VendorAddress.objects.create(
            vendor=self.vendor_a,
            address_type="WAREHOUSE",
            full_name="A Wh",
            phone="9111111111",
            address_line_1="A Wh Address",
            city="Indore",
            state="MP",
            postal_code="452015"
        )

        self.vendor_b_user = User.objects.create_user(
            username="vend_b@example.com",
            email="vend_b@example.com",
            password="Password123!",
            role="VENDOR"
        )
        self.vendor_b = Vendor.objects.create(
            user=self.vendor_b_user,
            store_name="Vendor B Store",
            business_name="Vendor B Inc",
            business_email="vend_b@example.com",
            business_phone="9222222222",
            status="APPROVED"
        )

        self.unapproved_vendor_user = User.objects.create_user(
            username="vend_pending@example.com",
            email="vend_pending@example.com",
            password="Password123!",
            role="VENDOR"
        )
        self.unapproved_vendor = Vendor.objects.create(
            user=self.unapproved_vendor_user,
            store_name="Pending Vendor Store",
            business_name="Pending Vendor Inc",
            business_email="vend_pending@example.com",
            business_phone="9333333333",
            status="PENDING"
        )

        self.admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="Password123!",
            role="ADMIN"
        )

        self.category = Category.objects.create(name="Items", slug="items")
        self.product = Product.objects.create(category=self.category, name="Item A", slug="item-a")
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="V1",
            sku="SKU-API-1",
            price=Decimal("1500.00"),
            stock=10,
            weight_kg=Decimal("0.500"),
            length_cm=Decimal("10.00"),
            width_cm=Decimal("10.00"),
            height_cm=Decimal("10.00")
        )

        self.master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.RAZORPAY,
            payment_status=Order.PAYMENT_PAID,
            subtotal=Decimal("1500.00"),
            total_amount=Decimal("1500.00")
        )

        self.vendor_order_a = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor_a,
            vendor_order_number="VORD-API-A-001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("1500.00"),
            total_amount=Decimal("1500.00")
        )
        OrderItem.objects.create(
            order=self.master_order,
            vendor_order=self.vendor_order_a,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("1500.00")
        )

    def test_unauthenticated_user_denied(self):
        url = f"/api/shipping/vendor-orders/{self.vendor_order_a.vendor_order_number}/create-shipment/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unapproved_vendor_denied(self):
        self.client.force_authenticate(user=self.unapproved_vendor_user)
        url = f"/api/shipping/vendor-orders/{self.vendor_order_a.vendor_order_number}/create-shipment/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_vendor_cannot_ship_another_vendors_order(self):
        self.client.force_authenticate(user=self.vendor_b_user)
        url = f"/api/shipping/vendor-orders/{self.vendor_order_a.vendor_order_number}/create-shipment/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_approved_vendor_creates_shipment_returns_201_then_200(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-API-1", provider_shipment_id="SR-API-S1")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-API-1", courier_company_id="10", courier_name="Delhivery")

        self.client.force_authenticate(user=self.vendor_a_user)
        url = f"/api/shipping/vendor-orders/{self.vendor_order_a.vendor_order_number}/create-shipment/"

        # 1st Call -> 201 CREATED
        res1 = self.client.post(url, {"courier_id": "10"}, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res1.data["success"])
        self.assertEqual(res1.data["shipment"]["awb_code"], "AWB-API-1")

        # 2nd Call (Idempotent) -> 200 OK
        res2 = self.client.post(url, {"courier_id": "10"}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertTrue(res2.data["success"])
        self.assertEqual(res2.data["shipment"]["awb_code"], "AWB-API-1")

    @patch.object(ShiprocketProvider, "assign_awb")
    @patch.object(ShiprocketProvider, "create_order")
    def test_admin_can_create_shipment(self, mock_create, mock_assign):
        mock_create.return_value = ShipmentCreateResult(provider_order_id="SR-ADM-1", provider_shipment_id="SR-ADM-S1")
        mock_assign.return_value = AWBAssignmentResult(awb_code="AWB-ADM-1", courier_company_id="10", courier_name="Delhivery")

        self.client.force_authenticate(user=self.admin_user)
        url = f"/api/shipping/vendor-orders/{self.vendor_order_a.vendor_order_number}/create-shipment/"
        res = self.client.post(url, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)




class Phase5ShippingLifecycleTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="cust_p5@example.com",
            email="cust_p5@example.com",
            password="Password123!",
            first_name="Customer",
            last_name="Five",
            role="CUSTOMER"
        )
        self.address = Address.objects.create(
            user=self.customer,
            full_name="Customer Five",
            phone="9988776655",
            address_line_1="Building 5, Street 9",
            city="Bhopal",
            state="Madhya Pradesh",
            postal_code="462001",
            country="India"
        )
        self.vendor_user = User.objects.create_user(
            username="vendor_p5@example.com",
            email="vendor_p5@example.com",
            password="Password123!",
            first_name="Vendor",
            last_name="Five",
            role="VENDOR"
        )
        self.vendor = Vendor.objects.create(
            user=self.vendor_user,
            store_name="Phase 5 Store",
            status="APPROVED",
            # shiprocket_pickup_location_name removed
        )
        self.category = Category.objects.create(name="Tech", slug="tech-p5")
        self.product = Product.objects.create(category=self.category, name="Tablet", slug="tablet-p5")
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="128GB",
            sku="TAB-128-P5",
            price=Decimal("25000.00"),
            stock=20
        )
        self.master_order = Order.objects.create(
            user=self.customer,
            address=self.address,
            snapshot_full_name=self.address.full_name,
            snapshot_phone=self.address.phone,
            snapshot_address_line_1=self.address.address_line_1,
            snapshot_city=self.address.city,
            snapshot_state=self.address.state,
            snapshot_postal_code=self.address.postal_code,
            status=Order.CONFIRMED,
            payment_method=Order.COD,
            payment_status=Order.PAYMENT_PENDING,
            subtotal=Decimal("25000.00"),
            total_amount=Decimal("25000.00")
        )
        self.vendor_order = VendorOrder.objects.create(
            master_order=self.master_order,
            vendor=self.vendor,
            vendor_order_number="VORD-P5-1001",
            status=VendorOrder.CONFIRMED,
            subtotal=Decimal("25000.00"),
            total_amount=Decimal("25000.00")
        )
        OrderItem.objects.create(
            order=self.master_order,
            vendor_order=self.vendor_order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            variant_name=self.variant.name,
            sku=self.variant.sku,
            price=self.variant.price,
            quantity=1,
            total=Decimal("25000.00")
        )

        self.shipment = Shipment.objects.create(
            vendor_order=self.vendor_order,
            provider_name="shiprocket",
            provider_order_id="SR-P5-ORD1",
            provider_shipment_id="SR-P5-SHIP1",
            courier_id="10",
            courier_name="Delhivery",
            awb_code="AWB-P5-9999",
            pickup_location_name="Phase5_Warehouse",
            status=Shipment.AWB_ASSIGNED,
        )

    @patch("apps.shipping.providers.shiprocket.ShiprocketProvider.request_pickup")
    def test_request_pickup_success(self, mock_pickup):
        from apps.shipping.dataclasses import PickupResult
        mock_pickup.return_value = PickupResult(
            pickup_status="1",
            pickup_token_number="PU_P5_TOKEN_123",
            scheduled_date="2026-09-18 10:00:00"
        )
        updated_shipment, is_new = ShippingService.request_pickup(self.shipment)
        self.assertTrue(is_new)
        self.assertEqual(updated_shipment.status, Shipment.PICKUP_REQUESTED)
        self.assertIsNotNone(updated_shipment.pickup_scheduled_at)

    def test_request_pickup_duplicate_state(self):
        self.shipment.status = Shipment.PICKUP_REQUESTED
        self.shipment.save()
        res_shipment, is_new = ShippingService.request_pickup(self.shipment)
        self.assertFalse(is_new)
        self.assertEqual(res_shipment.status, Shipment.PICKUP_REQUESTED)

    def test_request_pickup_invalid_state(self):
        self.shipment.status = Shipment.PENDING
        self.shipment.save()
        with self.assertRaises(ShippingValidationError):
            ShippingService.request_pickup(self.shipment)

    @patch("apps.shipping.providers.shiprocket.ShiprocketProvider.generate_label")
    def test_generate_label_success_and_caching(self, mock_label):
        from apps.shipping.dataclasses import LabelResult
        mock_label.return_value = LabelResult(
            label_created=True,
            label_url="https://apiv2.shiprocket.in/v1/external/labels/p5_label.pdf"
        )

        # 1st call -> provider invoked
        url1 = ShippingService.generate_label(self.shipment)
        self.assertEqual(url1, "https://apiv2.shiprocket.in/v1/external/labels/p5_label.pdf")
        self.assertEqual(mock_label.call_count, 1)

        # 2nd call -> returned from cache immediately
        url2 = ShippingService.generate_label(self.shipment)
        self.assertEqual(url2, "https://apiv2.shiprocket.in/v1/external/labels/p5_label.pdf")
        self.assertEqual(mock_label.call_count, 1)

    @patch("apps.shipping.providers.shiprocket.ShiprocketProvider.generate_manifest")
    def test_generate_manifest_success_and_caching(self, mock_manifest):
        from apps.shipping.dataclasses import ManifestResult
        mock_manifest.return_value = ManifestResult(
            status="1",
            manifest_url="https://apiv2.shiprocket.in/v1/external/manifests/p5_manifest.pdf"
        )
        self.shipment.status = Shipment.PICKUP_REQUESTED
        self.shipment.save()

        # 1st call -> provider invoked
        url1 = ShippingService.generate_manifest(self.shipment)
        self.assertEqual(url1, "https://apiv2.shiprocket.in/v1/external/manifests/p5_manifest.pdf")
        self.assertEqual(mock_manifest.call_count, 1)

        # 2nd call -> returned from cache immediately
        url2 = ShippingService.generate_manifest(self.shipment)
        self.assertEqual(url2, "https://apiv2.shiprocket.in/v1/external/manifests/p5_manifest.pdf")
        self.assertEqual(mock_manifest.call_count, 1)

    def test_process_tracking_event_valid_advancement(self):
        event_data = {
            "awb": "AWB-P5-9999",
            "current_status": "Delivered",
            "current_status_id": 7,
            "location": "Bhopal Hub",
            "activity": "Package delivered to recipient",
            "event_id": "EVT-DELIV-001",
        }
        self.shipment.status = Shipment.OUT_FOR_DELIVERY
        self.shipment.save()

        shipment, cat = ShippingService.process_tracking_event(self.shipment, event_data)
        self.assertEqual(cat, "VALID_ADVANCEMENT")
        self.assertEqual(shipment.status, Shipment.DELIVERED)
        self.assertEqual(shipment.vendor_order.status, VendorOrder.DELIVERED)
        self.assertEqual(shipment.vendor_order.master_order.status, Order.DELIVERED)

    def test_process_tracking_event_duplicate_fingerprint(self):
        event_data = {
            "awb": "AWB-P5-9999",
            "current_status": "Picked Up",
            "current_status_id": 5,
            "location": "Indore Hub",
            "activity": "Picked up from seller",
            "event_id": "EVT-PICK-001",
        }
        self.shipment.status = Shipment.PICKUP_REQUESTED
        self.shipment.save()

        # 1st call -> VALID_ADVANCEMENT
        s1, c1 = ShippingService.process_tracking_event(self.shipment, event_data)
        self.assertEqual(c1, "VALID_ADVANCEMENT")
        self.assertEqual(s1.status, Shipment.PICKED_UP)

        # 2nd call -> DUPLICATE
        s2, c2 = ShippingService.process_tracking_event(self.shipment, event_data)
        self.assertEqual(c2, "DUPLICATE")

    def test_process_tracking_event_out_of_order(self):
        self.shipment.status = Shipment.IN_TRANSIT
        self.shipment.save()

        # Try to pass an earlier state event (AWB Assigned)
        event_data = {
            "awb": "AWB-P5-9999",
            "current_status": "AWB Assigned",
            "current_status_id": 1,
            "location": "Origin Hub",
            "activity": "AWB assigned",
            "event_id": "EVT-AWB-001",
        }
        shipment, cat = ShippingService.process_tracking_event(self.shipment, event_data)
        self.assertEqual(cat, "OUT_OF_ORDER")
        self.assertEqual(shipment.status, Shipment.IN_TRANSIT)

    def test_process_tracking_event_stale(self):
        # Stale timestamp (2 days ago)
        event_data = {
            "awb": "AWB-P5-9999",
            "current_status": "In Transit",
            "current_status_id": 5,
            "location": "Transit Hub",
            "activity": "In transit scan",
            "activity_timestamp": "2024-01-01T10:00:00",
            "event_id": "EVT-STALE-001",
        }
        shipment, cat = ShippingService.process_tracking_event(self.shipment, event_data)
        self.assertEqual(cat, "STALE")

    @override_settings(SHIPROCKET_WEBHOOK_HEADER_NAME="HTTP_X_SHIPROCKET_TOKEN", SHIPROCKET_WEBHOOK_SECRET="test_secret_123")
    def test_shiprocket_webhook_authentication(self):
        url = "/api/shipping/webhooks/shiprocket/"
        payload = {
            "awb": "AWB-P5-9999",
            "current_status": "In Transit",
            "current_status_id": 5,
            "event_id": "EVT-WEBHOOK-101"
        }

        # 1. Invalid Token -> 401 Unauthorized
        res_invalid = self.client.post(url, payload, format="json", HTTP_X_SHIPROCKET_TOKEN="wrong_secret")
        self.assertEqual(res_invalid.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Valid Token -> 200 OK
        res_valid = self.client.post(url, payload, format="json", HTTP_X_SHIPROCKET_TOKEN="test_secret_123")
        self.assertEqual(res_valid.status_code, status.HTTP_200_OK)
        self.assertEqual(res_valid.data["status"], "ok")


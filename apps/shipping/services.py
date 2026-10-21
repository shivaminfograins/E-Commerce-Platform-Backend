from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.cache import cache
import hashlib
import hashlib
import json
import logging
import time
from decimal import Decimal
from datetime import datetime
from typing import Tuple, Optional, Dict, Any, List

from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.orders.models import VendorOrder, Order
from apps.orders.services import reconcile_master_order_status
from apps.shipping.models import Shipment, ShipmentStatusHistory
from apps.shipping.dataclasses import (
    ShipmentCreateRequest,
    ShipmentItem,
)
from apps.shipping.exceptions import (
    ShippingValidationError,
    ShippingConcurrencyError,
    ShippingProviderError,
)
from apps.shipping.providers import get_shipping_provider

logger = logging.getLogger(__name__)


def resolve_vendor_pickup_location_name(vendor) -> str:
    loc_name = getattr(vendor, "shiprocket_pickup_location_name", "") or getattr(vendor, "pickup_location_name", "")
    if not loc_name:
        store_name = getattr(vendor, "store_name", "Primary")
        loc_name = f"{store_name} Warehouse"
    return loc_name


def compute_event_fingerprint(
    awb_code: str,
    status_id: Any,
    status_str: str,
    activity_timestamp: str,
    location: str,
    activity_description: str
) -> str:
    raw = f"{awb_code}|{status_id}|{status_str}|{activity_timestamp}|{location}|{activity_description}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ShippingService:
    @classmethod
    def generate_cache_key(
        cls,
        origin_pincode: str,
        destination_pincode: str,
        weight: Decimal,
        length: Optional[Decimal] = None,
        width: Optional[Decimal] = None,
        height: Optional[Decimal] = None,
        declared_value: Optional[Decimal] = None,
        is_cod: bool = False,
    ) -> str:
        raw_key = (
            f"serv:{origin_pincode}:{destination_pincode}:{weight}:"
            f"{length}:{width}:{height}:{declared_value}:{is_cod}"
        )
        return f"shipping:serviceability:{hashlib.md5(raw_key.encode('utf-8')).hexdigest()}"

    """
    Central domain service for managing physical package shipping, external
    provider order creation, pickup scheduling, document generation, tracking
    event processing, and order reconciliation.
    """

    LOCK_TTL_SECONDS = 30.0

    @classmethod
    def check_serviceability_for_vendor_order(
        cls,
        vendor_order,
        is_cod: bool = False,
        provider_name: str = "shiprocket"
    ):
        if isinstance(vendor_order, (str, int)):
            from apps.orders.models import VendorOrder
            vendor_order = VendorOrder.objects.select_related(
                'order', 'vendor'
            ).prefetch_related('items__variant', 'items__product').get(pk=vendor_order)

        (
            weight,
            length,
            width,
            height,
            declared_val,
            origin_pincode,
            dest_pincode
        ) = cls.calculate_vendor_order_package(vendor_order)

        cache_key = f"ship_serv:{vendor_order.id}:{origin_pincode}:{dest_pincode}:{weight}:{is_cod}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        provider = get_shipping_provider(provider_name)
        result = provider.check_serviceability(
            origin_pincode=origin_pincode,
            destination_pincode=dest_pincode,
            weight=weight,
            length=length,
            width=width,
            height=height,
            declared_value=declared_val,
            is_cod=is_cod,
        )
        cache.set(cache_key, result, timeout=300)
        return result

    @classmethod
    def calculate_vendor_order_package(cls, vendor_order):
        if hasattr(vendor_order.items, "all"):
            qs = vendor_order.items.all()
            if hasattr(qs, "select_related"):
                qs = qs.select_related("variant", "product")
            items = list(qs)
        else:
            items = list(vendor_order.items)

        if not items:
            raise ShippingValidationError(
                f"VendorOrder '{vendor_order.vendor_order_number}' has no items."
            )

        total_weight = Decimal("0.000")
        max_length = Decimal("0.00")
        max_width = Decimal("0.00")
        total_height = Decimal("0.00")
        declared_val = Decimal("0.00")

        for item in items:
            variant = item.variant
            if not variant:
                item_name = getattr(item, "product_name", None) or getattr(item, "name", "Item")
                raise ShippingValidationError(
                    f"Product variant information is missing for item '{item_name}'."
                )

            qty = Decimal(str(item.quantity))
            sku = getattr(variant, "sku", "") or getattr(item, "sku", "UNKNOWN")

            v_weight = variant.weight_kg
            v_length = variant.length_cm
            v_width = variant.width_cm
            v_height = variant.height_cm

            if v_weight is None or Decimal(str(v_weight)) <= Decimal("0.000"):
                raise ShippingValidationError(
                    f"Shipping weight is missing or invalid for SKU '{sku}'."
                )
            if v_length is None or Decimal(str(v_length)) <= Decimal("0.00"):
                raise ShippingValidationError(
                    f"Shipping length is missing or invalid for SKU '{sku}'."
                )
            if v_width is None or Decimal(str(v_width)) <= Decimal("0.00"):
                raise ShippingValidationError(
                    f"Shipping width is missing or invalid for SKU '{sku}'."
                )
            if v_height is None or Decimal(str(v_height)) <= Decimal("0.00"):
                raise ShippingValidationError(
                    f"Shipping height is missing or invalid for SKU '{sku}'."
                )

            v_weight = Decimal(str(v_weight))
            v_length = Decimal(str(v_length))
            v_width = Decimal(str(v_width))
            v_height = Decimal(str(v_height))
            v_price = variant.price if variant.price is not None else getattr(item, 'price', Decimal("0.00"))

            total_weight += v_weight * qty
            if v_length > max_length:
                max_length = v_length
            if v_width > max_width:
                max_width = v_width
            total_height += v_height * qty

            item_total = getattr(item, 'total', None)
            if item_total is None:
                item_total = v_price * qty
            declared_val += Decimal(str(item_total))

        if total_weight <= Decimal("0.000"):
            raise ShippingValidationError(
                f"Total package weight for VendorOrder '{vendor_order.vendor_order_number}' must be greater than 0 kg."
            )

        # Extract Origin Pincode strictly
        origin_pincode = None
        vendor = getattr(vendor_order, "vendor", None)
        if vendor:
            if hasattr(vendor, "addresses"):
                w_addr = vendor.addresses.filter(address_type="WAREHOUSE").first() or vendor.addresses.first()
                if w_addr:
                    origin_pincode = getattr(w_addr, "postal_code", None) or getattr(w_addr, "pincode", None)

            if not origin_pincode:
                profile = getattr(vendor, "profile", None) or getattr(vendor, "vendorprofile", None)
                if profile:
                    origin_pincode = getattr(profile, "pincode", None) or getattr(profile, "postal_code", None)

            if not origin_pincode and hasattr(vendor, "shipping_addresses"):
                s_addr = vendor.shipping_addresses.filter(is_pickup_location=True).first() or vendor.shipping_addresses.first()
                if s_addr:
                    origin_pincode = getattr(s_addr, "pincode", None) or getattr(s_addr, "postal_code", None)

        origin_pincode = str(origin_pincode).strip() if origin_pincode else ""
        if not origin_pincode or not origin_pincode.isdigit() or len(origin_pincode) != 6:
            raise ShippingValidationError(
                f"Pickup location pincode for Vendor '{getattr(vendor, 'store_name', vendor)}' is missing or invalid."
            )

        # Extract Destination Pincode strictly
        dest_pincode = None
        master_order = getattr(vendor_order, "master_order", None) or getattr(vendor_order, "order", None)
        if master_order:
            dest_pincode = getattr(master_order, "snapshot_postal_code", None)
            if not dest_pincode and hasattr(master_order, "shipping_address") and master_order.shipping_address:
                dest_pincode = getattr(master_order.shipping_address, "postal_code", None) or getattr(master_order.shipping_address, "pincode", None)

        dest_pincode = str(dest_pincode).strip() if dest_pincode else ""
        if not dest_pincode or not dest_pincode.isdigit() or len(dest_pincode) != 6:
            raise ShippingValidationError(
                f"Destination shipping pincode for Order '{master_order}' is missing or invalid."
            )

        return (
            total_weight,
            max_length,
            max_width,
            total_height,
            declared_val,
            origin_pincode,
            dest_pincode,
        )

    @classmethod
    def create_shipment(
        cls,
        vendor_order: VendorOrder,
        courier_id: Optional[str] = None,
        provider_name: str = "shiprocket",
        lock_ttl_seconds: float = 30.0,
    ) -> Tuple[Shipment, bool]:
        attempt_id = f"req_{int(time.time() * 1000)}"
        now_ts = time.time()

        with transaction.atomic():
            locked_vo = VendorOrder.objects.select_for_update().get(pk=vendor_order.pk)
            existing_shipment = getattr(locked_vo, "shipment", None)

            if existing_shipment:
                if existing_shipment.status == Shipment.CANCELLED:
                    raise ShippingValidationError("Cannot create shipment for a cancelled shipment record.")
                if existing_shipment.provider_order_id and existing_shipment.provider_shipment_id and existing_shipment.awb_code:
                    return existing_shipment, False

                meta = existing_shipment.metadata or {}
                ops = meta.get("shipping_operations", {})
                creation_op = ops.get("creation", {})
                active_attempt = creation_op.get("attempt_id") or meta.get("creation_attempt_id")
                started_at = creation_op.get("started_at") or meta.get("creation_started_at", 0)

                if active_attempt and (now_ts - float(started_at)) < lock_ttl_seconds:
                    raise ShippingConcurrencyError(
                        "A shipment creation request for this order is currently in progress. Please try again shortly."
                    )

                if "shipping_operations" not in meta:
                    meta["shipping_operations"] = {}
                meta["shipping_operations"]["creation"] = {
                    "attempt_id": attempt_id,
                    "started_at": now_ts,
                }
                meta["creation_attempt_id"] = attempt_id
                meta["creation_started_at"] = now_ts

                existing_shipment.metadata = meta
                existing_shipment.save(update_fields=["metadata", "updated_at"])
                shipment = existing_shipment
                is_new_shipment = False
            else:
                weight, length, width, height, declared_val, origin_pin, dest_pin = cls.calculate_vendor_order_package(locked_vo)
                pickup_loc = resolve_vendor_pickup_location_name(locked_vo.vendor)

                items_snapshot = []
                for item in locked_vo.items.all():
                    items_snapshot.append({
                        "sku": getattr(item, "sku", ""),
                        "product_name": getattr(item, "product_name", ""),
                        "variant_name": getattr(item, "variant_name", ""),
                        "quantity": getattr(item, "quantity", 1),
                        "price": str(getattr(item, "price", "0.00")),
                    })

                metadata_snapshot = {
                    "origin_pincode": origin_pin,
                    "destination_pincode": dest_pin,
                    "items": items_snapshot,
                    "creation_attempt_id": attempt_id,
                    "creation_started_at": now_ts,
                    "shipping_operations": {
                        "creation": {
                            "attempt_id": attempt_id,
                            "started_at": now_ts,
                        }
                    }
                }

                shipment = Shipment.objects.create(
                    vendor_order=locked_vo,
                    provider_name=provider_name,
                    weight_kg=weight,
                    length_cm=length,
                    width_cm=width,
                    height_cm=height,
                    pickup_location_name=pickup_loc,
                    status=Shipment.PENDING,
                    metadata=metadata_snapshot,
                )
                is_new_shipment = True

        provider = get_shipping_provider(provider_name)
        master_order = vendor_order.master_order

        try:
            if not shipment.provider_order_id or not shipment.provider_shipment_id:
                shipment_items = []
                for item in vendor_order.items.all():
                    shipment_items.append(
                        ShipmentItem(
                            name=getattr(item, "product_name", "Item"),
                            sku=getattr(item, "sku", "SKU"),
                            units=int(getattr(item, "quantity", 1)),
                            selling_price=Decimal(str(getattr(item, "price", "0.00"))),
                        )
                    )

                customer_email = getattr(master_order.user, "email", "") if hasattr(master_order, "user") and master_order.user else ""
                is_cod = getattr(master_order, "payment_method", "").lower() == "cod"

                req = ShipmentCreateRequest(
                    order_id=vendor_order.vendor_order_number,
                    order_date=vendor_order.created_at.strftime("%Y-%m-%d %H:%M"),
                    pickup_location=shipment.pickup_location_name,
                    billing_customer_name=master_order.snapshot_full_name,
                    billing_address=master_order.snapshot_address_line_1,
                    billing_address_2=master_order.snapshot_address_line_2 or "",
                    billing_city=master_order.snapshot_city,
                    billing_pincode=master_order.snapshot_postal_code,
                    billing_state=master_order.snapshot_state,
                    billing_country=master_order.snapshot_country or "India",
                    billing_phone=master_order.snapshot_phone,
                    billing_email=customer_email,
                    shipping_is_billing=True,
                    order_items=shipment_items,
                    payment_method="COD" if is_cod else "Prepaid",
                    sub_total=vendor_order.total_amount,
                    weight=shipment.weight_kg,
                    length=shipment.length_cm,
                    width=shipment.width_cm,
                    height=shipment.height_cm,
                )

                create_res = provider.create_order(req)
                shipment.provider_order_id = create_res.provider_order_id
                shipment.provider_shipment_id = create_res.provider_shipment_id
                shipment.save(update_fields=["provider_order_id", "provider_shipment_id", "updated_at"])
                shipment.transition_to(
                    Shipment.CREATED,
                    remarks="Order successfully created in external provider system",
                    raw_payload={"provider_order_id": create_res.provider_order_id, "provider_shipment_id": create_res.provider_shipment_id},
                )

            if not shipment.awb_code:
                awb_res = provider.assign_awb(shipment.provider_shipment_id, courier_id=courier_id)
                shipment.courier_id = awb_res.courier_company_id
                shipment.courier_name = awb_res.courier_name
                shipment.awb_code = awb_res.awb_code
                shipment.save(update_fields=["courier_id", "courier_name", "awb_code", "updated_at"])

                if shipment.status == Shipment.CREATED:
                    shipment.transition_to(Shipment.COURIER_ASSIGNED, remarks=f"Courier assigned: {awb_res.courier_name}")
                    shipment.transition_to(Shipment.AWB_ASSIGNED, remarks=f"AWB generated: {awb_res.awb_code}")
                elif shipment.status == Shipment.COURIER_ASSIGNED:
                    shipment.transition_to(Shipment.AWB_ASSIGNED, remarks=f"AWB generated: {awb_res.awb_code}")
                elif shipment.status == Shipment.FAILED:
                    shipment.transition_to(Shipment.CREATED, remarks="Retrying from FAILED state")
                    shipment.transition_to(Shipment.COURIER_ASSIGNED, remarks=f"Courier assigned: {awb_res.courier_name}")
                    shipment.transition_to(Shipment.AWB_ASSIGNED, remarks=f"AWB generated: {awb_res.awb_code}")
        except Exception as exc:
            if shipment.can_transition_to(Shipment.FAILED):
                shipment.transition_to(Shipment.FAILED, remarks=f"Operation failed: {exc}")
            raise exc
        finally:
            if shipment and shipment.pk:
                try:
                    with transaction.atomic():
                        current_shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                        meta = current_shipment.metadata or {}
                        if "shipping_operations" in meta and "creation" in meta["shipping_operations"]:
                            if meta["shipping_operations"]["creation"].get("attempt_id") == attempt_id:
                                meta["shipping_operations"]["creation"].pop("attempt_id", None)
                                meta["shipping_operations"]["creation"].pop("started_at", None)
                        if meta.get("creation_attempt_id") == attempt_id:
                            meta.pop("creation_attempt_id", None)
                            meta.pop("creation_started_at", None)
                        current_shipment.metadata = meta
                        current_shipment.save(update_fields=["metadata", "updated_at"])
                except Exception as clean_exc:
                    logger.warning("Failed to clean up creation attempt lock: %s", clean_exc)

        return shipment, is_new_shipment

    @classmethod
    def request_pickup(cls, shipment: Shipment) -> Tuple[Shipment, bool]:
        """
        Schedules courier pickup for an existing shipment in AWB_ASSIGNED status.
        Uses operation-level concurrency protection and response state caching.
        """
        if shipment.status in [Shipment.PICKUP_REQUESTED, Shipment.PICKED_UP, Shipment.IN_TRANSIT, Shipment.OUT_FOR_DELIVERY, Shipment.DELIVERED]:
            return shipment, False

        if not shipment.can_transition_to(Shipment.PICKUP_REQUESTED):
            raise ShippingValidationError(
                f"Cannot request pickup for shipment in status '{shipment.get_status_display()}' ({shipment.status}). Must be AWB_ASSIGNED."
            )

        attempt_id = f"pick_{int(time.time() * 1000)}"
        now_ts = time.time()

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
            meta = shipment.metadata or {}
            ops = meta.get("shipping_operations", {})
            pickup_op = ops.get("pickup", {})

            active_attempt = pickup_op.get("attempt_id")
            started_at = pickup_op.get("started_at", 0)

            if active_attempt and (now_ts - float(started_at)) < cls.LOCK_TTL_SECONDS:
                raise ShippingConcurrencyError(
                    "A pickup request for this shipment is currently in progress. Please try again shortly."
                )

            if "shipping_operations" not in meta:
                meta["shipping_operations"] = {}

            meta["shipping_operations"]["pickup"] = {
                "attempt_id": attempt_id,
                "started_at": now_ts,
            }
            shipment.metadata = meta
            shipment.save(update_fields=["metadata", "updated_at"])

        provider = get_shipping_provider(shipment.provider_name)
        try:
            pickup_res = provider.request_pickup([shipment.provider_shipment_id])
            now_dt = timezone.now()

            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" not in meta:
                    meta["shipping_operations"] = {}

                meta["shipping_operations"]["pickup"] = {
                    "token_number": pickup_res.pickup_token_number,
                    "scheduled_date": pickup_res.scheduled_date,
                    "requested_at": now_dt.isoformat(),
                    "last_error": None,
                }
                shipment.metadata = meta
                shipment.pickup_scheduled_at = now_dt
                shipment.save(update_fields=["metadata", "pickup_scheduled_at", "updated_at"])

                shipment.transition_to(
                    Shipment.PICKUP_REQUESTED,
                    remarks=f"Pickup scheduled. Token: {pickup_res.pickup_token_number}",
                    raw_payload=pickup_res.raw_response,
                )
            return shipment, True
        except Exception as exc:
            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" in meta and "pickup" in meta["shipping_operations"]:
                    meta["shipping_operations"]["pickup"]["last_error"] = str(exc)
                    meta["shipping_operations"]["pickup"].pop("attempt_id", None)
                    meta["shipping_operations"]["pickup"].pop("started_at", None)
                    shipment.metadata = meta
                    shipment.save(update_fields=["metadata", "updated_at"])
            raise exc

    @classmethod
    def generate_label(cls, shipment: Shipment) -> str:
        """
        Retrieves or generates shipping label PDF URL for a shipment.
        Cached idempotently if label_url already exists.
        """
        if shipment.label_url:
            return shipment.label_url

        if shipment.status in [Shipment.PENDING, Shipment.CREATED, Shipment.COURIER_ASSIGNED, Shipment.CANCELLED]:
            raise ShippingValidationError(
                f"Cannot generate label for shipment in status '{shipment.get_status_display()}'. Must be AWB_ASSIGNED or later."
            )

        attempt_id = f"lbl_{int(time.time() * 1000)}"
        now_ts = time.time()

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
            if shipment.label_url:
                return shipment.label_url

            meta = shipment.metadata or {}
            ops = meta.get("shipping_operations", {})
            lbl_op = ops.get("label", {})

            active_attempt = lbl_op.get("attempt_id")
            started_at = lbl_op.get("started_at", 0)

            if active_attempt and (now_ts - float(started_at)) < cls.LOCK_TTL_SECONDS:
                raise ShippingConcurrencyError(
                    "Label generation for this shipment is currently in progress. Please try again shortly."
                )

            if "shipping_operations" not in meta:
                meta["shipping_operations"] = {}

            meta["shipping_operations"]["label"] = {
                "attempt_id": attempt_id,
                "started_at": now_ts,
            }
            shipment.metadata = meta
            shipment.save(update_fields=["metadata", "updated_at"])

        provider = get_shipping_provider(shipment.provider_name)
        try:
            label_res = provider.generate_label([shipment.provider_shipment_id])
            now_dt = timezone.now()

            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" not in meta:
                    meta["shipping_operations"] = {}

                meta["shipping_operations"]["label"] = {
                    "url": label_res.label_url,
                    "generated_at": now_dt.isoformat(),
                    "last_error": None,
                }
                shipment.metadata = meta
                shipment.label_url = label_res.label_url
                shipment.save(update_fields=["label_url", "metadata", "updated_at"])

            return shipment.label_url
        except Exception as exc:
            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" in meta and "label" in meta["shipping_operations"]:
                    meta["shipping_operations"]["label"]["last_error"] = str(exc)
                    meta["shipping_operations"]["label"].pop("attempt_id", None)
                    meta["shipping_operations"]["label"].pop("started_at", None)
                    shipment.metadata = meta
                    shipment.save(update_fields=["metadata", "updated_at"])
            raise exc

    @classmethod
    def generate_manifest(cls, shipment: Shipment) -> str:
        """
        Retrieves or generates pickup manifest PDF URL for a shipment.
        Cached idempotently if manifest_url already exists.
        """
        if shipment.manifest_url:
            return shipment.manifest_url

        if shipment.status not in [Shipment.PICKUP_REQUESTED, Shipment.PICKED_UP, Shipment.IN_TRANSIT, Shipment.OUT_FOR_DELIVERY, Shipment.DELIVERED]:
            raise ShippingValidationError(
                f"Cannot generate manifest for shipment in status '{shipment.get_status_display()}'. Must be PICKUP_REQUESTED or later."
            )

        attempt_id = f"mnf_{int(time.time() * 1000)}"
        now_ts = time.time()

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
            if shipment.manifest_url:
                return shipment.manifest_url

            meta = shipment.metadata or {}
            ops = meta.get("shipping_operations", {})
            mnf_op = ops.get("manifest", {})

            active_attempt = mnf_op.get("attempt_id")
            started_at = mnf_op.get("started_at", 0)

            if active_attempt and (now_ts - float(started_at)) < cls.LOCK_TTL_SECONDS:
                raise ShippingConcurrencyError(
                    "Manifest generation for this shipment is currently in progress. Please try again shortly."
                )

            if "shipping_operations" not in meta:
                meta["shipping_operations"] = {}

            meta["shipping_operations"]["manifest"] = {
                "attempt_id": attempt_id,
                "started_at": now_ts,
            }
            shipment.metadata = meta
            shipment.save(update_fields=["metadata", "updated_at"])

        provider = get_shipping_provider(shipment.provider_name)
        try:
            manifest_res = provider.generate_manifest([shipment.provider_shipment_id])
            now_dt = timezone.now()

            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" not in meta:
                    meta["shipping_operations"] = {}

                meta["shipping_operations"]["manifest"] = {
                    "url": manifest_res.manifest_url,
                    "generated_at": now_dt.isoformat(),
                    "last_error": None,
                }
                shipment.metadata = meta
                shipment.manifest_url = manifest_res.manifest_url
                shipment.save(update_fields=["manifest_url", "metadata", "updated_at"])

            return shipment.manifest_url
        except Exception as exc:
            with transaction.atomic():
                shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
                meta = shipment.metadata or {}
                if "shipping_operations" in meta and "manifest" in meta["shipping_operations"]:
                    meta["shipping_operations"]["manifest"]["last_error"] = str(exc)
                    meta["shipping_operations"]["manifest"].pop("attempt_id", None)
                    meta["shipping_operations"]["manifest"].pop("started_at", None)
                    shipment.metadata = meta
                    shipment.save(update_fields=["metadata", "updated_at"])
            raise exc

    @classmethod
    def process_tracking_event(cls, shipment: Shipment, event_data: dict) -> Tuple[Shipment, str]:
        """
        Unified tracking event processor for Webhook events and Polling sync.
        Enforces:
        1. Provider Event ID or Deterministic Fingerprint Deduplication
        2. Two-Tier Validation (Timestamp Check + State Machine Allowed Matrix)
        3. 5-Category Taxonomy Classification:
           DUPLICATE, STALE, OUT_OF_ORDER, UNKNOWN_STATUS, VALID_ADVANCEMENT
        4. VendorOrder and Master Order reconciliation on VALID_ADVANCEMENT.
        """
        awb_code = str(event_data.get("awb") or event_data.get("awb_code") or shipment.awb_code or "")
        status_id = event_data.get("current_status_id") or event_data.get("status_id") or event_data.get("shipment_status")
        status_str = str(event_data.get("current_status") or event_data.get("status") or "")
        activity_ts = str(event_data.get("activity_timestamp") or event_data.get("date") or event_data.get("etd") or "")
        location = str(event_data.get("location") or "")
        activity_desc = str(event_data.get("activity") or event_data.get("remarks") or "")

        provider_event_id = str(event_data.get("event_id") or event_data.get("scan_id") or "")
        fingerprint = compute_event_fingerprint(
            awb_code=awb_code,
            status_id=status_id,
            status_str=status_str,
            activity_timestamp=activity_ts,
            location=location,
            activity_description=activity_desc,
        )

        with transaction.atomic():
            shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)

            # Step 1: Check for DUPLICATE in ShipmentStatusHistory
            existing_histories = list(shipment.status_history.all())
            for h in existing_histories:
                payload = h.raw_payload or {}
                if isinstance(payload, dict):
                    if provider_event_id and payload.get("provider_event_id") == provider_event_id:
                        return shipment, "DUPLICATE"
                    if payload.get("fingerprint") == fingerprint:
                        return shipment, "DUPLICATE"

            # Step 2: Map Provider Status to Internal Shipment Status Choice
            mapped_status = cls._map_provider_status(status_id, status_str)
            raw_payload = {
                "provider_event_id": provider_event_id,
                "fingerprint": fingerprint,
                "status_id": status_id,
                "status_str": status_str,
                "activity_timestamp": activity_ts,
                "location": location,
                "activity_description": activity_desc,
                "event_data": event_data,
            }

            if not mapped_status:
                ShipmentStatusHistory.objects.create(
                    shipment=shipment,
                    status=shipment.status,
                    location=location,
                    remarks=f"Unmapped provider tracking status: '{status_str}' (ID: {status_id})",
                    raw_payload=raw_payload,
                )
                return shipment, "UNKNOWN_STATUS"

            # Step 3: Check Event Timestamp (STALE check)
            is_stale = False
            if activity_ts:
                try:
                    event_dt = datetime.fromisoformat(activity_ts)
                    if timezone.is_naive(event_dt):
                        event_dt = timezone.make_aware(event_dt, dt_timezone.utc)
                    if shipment.created_at and event_dt < (shipment.created_at - timedelta(hours=24)):
                        is_stale = True
                    elif shipment.status_updated_at:
                        st_updated = shipment.status_updated_at
                        if timezone.is_naive(st_updated):
                            st_updated = timezone.make_aware(st_updated, dt_timezone.utc)
                        if event_dt <= st_updated:
                            is_stale = True
                except Exception as exc:
                    logger.warning("Error parsing activity_timestamp: %s", exc)

            if is_stale:
                ShipmentStatusHistory.objects.create(
                    shipment=shipment,
                    status=shipment.status,
                    location=location,
                    remarks=f"Stale tracking event recorded for status '{mapped_status}'",
                    raw_payload=raw_payload,
                )
                return shipment, "STALE"

            # Step 4: Check State Machine Transition Matrix (OUT_OF_ORDER check)
            if not shipment.can_transition_to(mapped_status):
                ShipmentStatusHistory.objects.create(
                    shipment=shipment,
                    status=shipment.status,
                    location=location,
                    remarks=f"Out-of-order tracking scan recorded for status '{mapped_status}'",
                    raw_payload=raw_payload,
                )
                return shipment, "OUT_OF_ORDER"

            # Step 5: VALID_ADVANCEMENT -> Execute Status Transition
            shipment.transition_to(
                mapped_status,
                location=location,
                remarks=activity_desc or f"Tracking update: {mapped_status}",
                raw_payload=raw_payload,
            )

            # Update Metadata Tracking Namespace
            meta = shipment.metadata or {}
            if "tracking" not in meta:
                meta["tracking"] = {}

            now_iso = timezone.now().isoformat()
            meta["tracking"]["last_provider_sync_at"] = now_iso
            meta["tracking"]["last_provider_event_at"] = activity_ts or now_iso
            meta["tracking"]["last_event_fingerprint"] = fingerprint
            shipment.metadata = meta

            # Update VendorOrder & Master Order Rollup
            vo = shipment.vendor_order
            now_dt = timezone.now()

            if mapped_status in [Shipment.PICKED_UP, Shipment.IN_TRANSIT, Shipment.OUT_FOR_DELIVERY]:
                if vo.status not in [VendorOrder.SHIPPED, VendorOrder.DELIVERED, VendorOrder.CANCELLED]:
                    vo.status = VendorOrder.SHIPPED
                    if not vo.shipped_at:
                        vo.shipped_at = now_dt
                    vo.save(update_fields=["status", "shipped_at", "updated_at"])

                if not shipment.shipped_at:
                    shipment.shipped_at = now_dt

            elif mapped_status == Shipment.DELIVERED:
                if vo.status != VendorOrder.DELIVERED:
                    vo.status = VendorOrder.DELIVERED
                    if not vo.delivered_at:
                        vo.delivered_at = now_dt
                    vo.save(update_fields=["status", "delivered_at", "updated_at"])

                if not shipment.delivered_at:
                    shipment.delivered_at = now_dt

            shipment.save(update_fields=["metadata", "shipped_at", "delivered_at", "updated_at"])
            reconcile_master_order_status(vo.master_order)

            return shipment, "VALID_ADVANCEMENT"

    @classmethod
    def _map_provider_status(cls, status_id: Any, status_str: str) -> Optional[str]:
        s_str = str(status_str or "").upper().strip()
        try:
            s_id = int(status_id) if status_id is not None else None
        except (ValueError, TypeError):
            s_id = None

        if s_id == 1 or s_str == "AWB ASSIGNED":
            return Shipment.AWB_ASSIGNED
        if s_id == 2 or s_str == "LABEL GENERATED":
            return Shipment.AWB_ASSIGNED
        if s_id in [3, 4] or s_str in ["PICKUP SCHEDULED", "PICKUP GENERATED", "OUT FOR PICKUP"]:
            return Shipment.PICKUP_REQUESTED
        if s_id in [5, 17, 18] or s_str in ["PICKED UP", "IN TRANSIT", "SHIPPED"]:
            return Shipment.PICKED_UP
        if s_id == 6 or s_str == "OUT FOR DELIVERY":
            return Shipment.OUT_FOR_DELIVERY
        if s_id == 7 or s_str == "DELIVERED":
            return Shipment.DELIVERED
        if s_id in [8, 11] or s_str == "CANCELED":
            return Shipment.CANCELLED
        if s_id in [9, 12, 13] or s_str in ["RTO INITIATED", "RTO IN TRANSIT"]:
            return Shipment.RTO_INITIATED
        if s_id in [10, 14] or s_str == "RTO DELIVERED":
            return Shipment.RTO_DELIVERED

        return None

    @classmethod
    def track_shipment(cls, shipment: Shipment) -> Shipment:
        """
        Polls live tracking status from provider and runs scans through process_tracking_event.
        """
        provider = get_shipping_provider(shipment.provider_name)
        res = provider.track(awb_code=shipment.awb_code, provider_shipment_id=shipment.provider_shipment_id)

        if res.scans:
            for scan in res.scans:
                event_data = {
                    "awb": res.awb_code,
                    "current_status": scan.status_name or res.current_status,
                    "current_status_id": scan.status_id or res.current_status_id,
                    "location": scan.location,
                    "activity": scan.activity,
                    "date": scan.date,
                    "etd": res.etd,
                }
                cls.process_tracking_event(shipment, event_data)
        elif res.current_status or res.current_status_id:
            event_data = {
                "awb": res.awb_code,
                "current_status": res.current_status,
                "current_status_id": res.current_status_id,
                "etd": res.etd,
            }
            cls.process_tracking_event(shipment, event_data)

        shipment.refresh_from_db()
        return shipment

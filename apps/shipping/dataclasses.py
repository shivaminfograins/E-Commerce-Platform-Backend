from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any


@dataclass
class CourierOption:
    courier_id: str
    courier_name: str
    shipping_charge: Decimal
    estimated_delivery_days: str
    estimated_delivery_date: str
    is_cod_available: bool
    rating: float = 0.0
    min_weight: Decimal = Decimal("0.500")


@dataclass
class ServiceabilityResult:
    is_serviceable: bool
    origin_pincode: str
    destination_pincode: str
    total_weight_kg: Decimal
    courier_options: List[CourierOption] = field(default_factory=list)
    recommended_courier: Optional[CourierOption] = None
    cheapest_courier: Optional[CourierOption] = None
    fastest_courier: Optional[CourierOption] = None
    raw_metadata: Optional[dict] = None


@dataclass
class ShipmentItem:
    name: str
    sku: str
    units: int
    selling_price: Decimal
    discount: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    hsn: str = ""


@dataclass
class ShipmentCreateRequest:
    order_id: str
    order_date: str
    pickup_location: str
    billing_customer_name: str
    billing_address: str
    billing_city: str
    billing_pincode: str
    billing_state: str
    billing_country: str
    billing_phone: str
    billing_email: str = ""
    billing_last_name: str = ""
    billing_address_2: str = ""
    shipping_is_billing: bool = True
    shipping_customer_name: str = ""
    shipping_last_name: str = ""
    shipping_address: str = ""
    shipping_address_2: str = ""
    shipping_city: str = ""
    shipping_pincode: str = ""
    shipping_state: str = ""
    shipping_country: str = "India"
    shipping_phone: str = ""
    shipping_email: str = ""
    order_items: List[ShipmentItem] = field(default_factory=list)
    payment_method: str = "Prepaid"  # "Prepaid" or "COD"
    sub_total: Decimal = Decimal("0.00")
    shipping_charges: Decimal = Decimal("0.00")
    giftwrap_charges: Decimal = Decimal("0.00")
    transaction_charges: Decimal = Decimal("0.00")
    total_discount: Decimal = Decimal("0.00")
    weight: Decimal = Decimal("0.000")
    length: Decimal = Decimal("0.00")
    width: Decimal = Decimal("0.00")
    height: Decimal = Decimal("0.00")


@dataclass
class ShipmentCreateResult:
    provider_order_id: str
    provider_shipment_id: str
    status: str = ""
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class AWBAssignmentResult:
    awb_code: str
    courier_company_id: str
    courier_name: str
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class PickupResult:
    pickup_status: str
    pickup_token_number: str = ''
    scheduled_date: str = ''
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class LabelResult:
    label_created: bool
    label_url: str = ''
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class ManifestResult:
    status: str
    manifest_url: str = ''
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class TrackingScan:
    activity: str
    location: str = ''
    date: str = ''
    status_id: Optional[int] = None
    status_name: Optional[str] = None


@dataclass
class TrackingResult:
    awb_code: str
    current_status: str = ''
    current_status_id: Optional[int] = None
    scans: List[TrackingScan] = field(default_factory=list)
    etd: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None

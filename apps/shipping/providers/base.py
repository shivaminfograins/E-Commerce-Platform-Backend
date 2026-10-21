from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional, List
from apps.shipping.dataclasses import (
    ServiceabilityResult,
    ShipmentCreateRequest,
    ShipmentCreateResult,
    AWBAssignmentResult,
    PickupResult,
    LabelResult,
    ManifestResult,
    TrackingResult,
)


class BaseShippingProvider(ABC):
    """
    Abstract Base Class defining provider-independent interface for shipping providers.
    All shipping integrations (Shiprocket, Delhivery, etc.) must inherit from this class.
    """

    @abstractmethod
    def authenticate(self):
        """
        Authenticate with the shipping provider API and return a valid access token.
        """
        pass

    @abstractmethod
    def check_serviceability(
        self,
        origin_pincode: str,
        destination_pincode: str,
        weight: Decimal,
        is_cod: bool = False,
        length: Optional[Decimal] = None,
        width: Optional[Decimal] = None,
        height: Optional[Decimal] = None,
        declared_value: Optional[Decimal] = None,
        **kwargs
    ) -> ServiceabilityResult:
        """
        Check delivery serviceability and courier options for a given route and package metrics.
        """
        pass

    @abstractmethod
    def create_order(
        self,
        request: ShipmentCreateRequest,
        **kwargs
    ) -> ShipmentCreateResult:
        """
        Create an order with the external shipping provider.
        """
        pass

    @abstractmethod
    def assign_awb(
        self,
        provider_shipment_id: str,
        courier_id: Optional[str] = None,
        **kwargs
    ) -> AWBAssignmentResult:
        """
        Assign courier and generate AWB number for an existing external shipment.
        """
        pass

    @abstractmethod
    def request_pickup(
        self,
        shipment_ids: List[str],
        **kwargs
    ) -> PickupResult:
        """
        Request pickup collection from courier for one or more shipments.
        """
        pass

    @abstractmethod
    def generate_label(
        self,
        shipment_ids: List[str],
        **kwargs
    ) -> LabelResult:
        """
        Generate shipping label PDF URL for one or more shipments.
        """
        pass

    @abstractmethod
    def generate_manifest(
        self,
        shipment_ids: List[str],
        **kwargs
    ) -> ManifestResult:
        """
        Generate pickup manifest PDF URL for one or more shipments.
        """
        pass

    @abstractmethod
    def track(
        self,
        awb_code: Optional[str] = None,
        provider_shipment_id: Optional[str] = None,
        **kwargs
    ) -> TrackingResult:
        """
        Fetch tracking details and scan activities for a shipment by AWB code or shipment ID.
        """
        pass

    @abstractmethod
    def cancel_shipment(
        self,
        provider_order_ids: List[str],
        **kwargs
    ):
        """
        Cancel external shipment orders with provider.
        """
        pass

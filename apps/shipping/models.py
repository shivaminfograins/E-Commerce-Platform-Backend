from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from apps.orders.models import VendorOrder


class Shipment(models.Model):
    """
    Represents a physical package/shipment associated with a Vendor sub-order.
    Decoupled from specific shipping provider APIs.
    """

    # Provider Identifier
    PROVIDER_SHIPROCKET = "shiprocket"
    PROVIDER_CHOICES = [
        (PROVIDER_SHIPROCKET, "Shiprocket"),
    ]

    # Internal Provider-Independent Status Lifecycle
    PENDING          = "PENDING"          # Created locally, not yet pushed to shipping provider
    CREATED          = "CREATED"          # Order successfully created in provider system
    COURIER_ASSIGNED = "COURIER_ASSIGNED" # Courier service selected for shipment
    AWB_ASSIGNED     = "AWB_ASSIGNED"     # AWB number generated and assigned
    PICKUP_REQUESTED = "PICKUP_REQUESTED" # Pickup schedule requested from courier
    PICKED_UP        = "PICKED_UP"        # Package collected by courier
    IN_TRANSIT       = "IN_TRANSIT"       # Package in transit to destination hub
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY" # Package out with delivery agent
    DELIVERED        = "DELIVERED"        # Package successfully delivered to customer
    CANCELLED        = "CANCELLED"        # Shipment cancelled prior to delivery
    RTO_INITIATED    = "RTO_INITIATED"    # Return to Origin initiated by carrier
    RTO_DELIVERED    = "RTO_DELIVERED"    # Returned to vendor warehouse
    FAILED           = "FAILED"           # Processing failure / provider exception

    STATUS_CHOICES = [
        (PENDING,          "Pending"),
        (CREATED,          "Created in Provider System"),
        (COURIER_ASSIGNED, "Courier Assigned"),
        (AWB_ASSIGNED,     "AWB Assigned"),
        (PICKUP_REQUESTED, "Pickup Scheduled"),
        (PICKED_UP,        "Picked Up"),
        (IN_TRANSIT,       "In Transit"),
        (OUT_FOR_DELIVERY, "Out for Delivery"),
        (DELIVERED,        "Delivered"),
        (CANCELLED,        "Cancelled"),
        (RTO_INITIATED,    "RTO Initiated"),
        (RTO_DELIVERED,    "RTO Delivered"),
        (FAILED,           "Failed"),
    ]

    # Allowed Status Transition Matrix (Provider-Independent Enforcement)
    ALLOWED_TRANSITIONS = {
        PENDING:          {CREATED, FAILED, CANCELLED},
        CREATED:          {COURIER_ASSIGNED, FAILED, CANCELLED},
        COURIER_ASSIGNED: {AWB_ASSIGNED, FAILED, CANCELLED},
        AWB_ASSIGNED:     {PICKUP_REQUESTED, FAILED, CANCELLED},
        PICKUP_REQUESTED: {PICKED_UP, FAILED, CANCELLED},
        PICKED_UP:        {IN_TRANSIT, RTO_INITIATED, FAILED},
        IN_TRANSIT:       {OUT_FOR_DELIVERY, RTO_INITIATED, FAILED},
        OUT_FOR_DELIVERY: {DELIVERED, RTO_INITIATED, FAILED},
        RTO_INITIATED:    {RTO_DELIVERED, FAILED},
        DELIVERED:        set(),  # Terminal state
        CANCELLED:        set(),  # Terminal state
        RTO_DELIVERED:    set(),  # Terminal state
        FAILED:           {PENDING, CREATED, CANCELLED},  # Allow retry from FAILED
    }

    vendor_order = models.OneToOneField(
        VendorOrder,
        on_delete=models.PROTECT,
        related_name="shipment",
        help_text="The Vendor sub-order this physical shipment fulfills."
    )

    provider_name = models.CharField(
        max_length=50,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_SHIPROCKET,
        db_index=True,
        help_text="Identifies the external shipping provider."
    )

    provider_order_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Order ID returned by external provider (e.g. Shiprocket order_id)."
    )

    provider_shipment_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Shipment ID returned by external provider (e.g. Shiprocket shipment_id)."
    )

    courier_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Numeric/alphanumeric identifier of chosen courier company."
    )

    courier_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Display name of assigned courier (e.g. Delhivery, Bluedart)."
    )

    awb_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Air Waybill (AWB) tracking number."
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True,
        help_text="Current internal shipment status."
    )

    # Package Dimensions Snapshot (Frozen at shipment creation time)
    weight_kg = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.500"),
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="Total package weight in kilograms."
    )

    length_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Package length in centimeters."
    )

    width_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Package width in centimeters."
    )

    height_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Package height in centimeters."
    )

    shipping_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Actual shipping cost charged by provider."
    )

    pickup_location_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Vendor pickup location nickname used for courier collection."
    )

    # Document & Tracking URLs
    label_url = models.URLField(max_length=1000, blank=True, default="")
    manifest_url = models.URLField(max_length=1000, blank=True, default="")
    tracking_url = models.URLField(max_length=1000, blank=True, default="")

    # Lifecycle Timestamps
    pickup_scheduled_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    metadata = models.JSONField(
        blank=True,
        null=True,
        help_text="Raw payload snapshot for provider audit and response logging."
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shipment"
        verbose_name_plural = "Shipments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider_name", "provider_order_id"], name="idx_shipment_prov_ord"),
            models.Index(fields=["provider_name", "provider_shipment_id"], name="idx_shipment_prov_ship"),
            models.Index(fields=["awb_code"], name="idx_shipment_awb"),
        ]

    def __str__(self):
        return f"Shipment for {self.vendor_order.vendor_order_number} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.weight_kg is not None and self.weight_kg <= Decimal("0.000"):
            raise ValidationError({"weight_kg": "Weight must be greater than 0 kg."})
        if self.length_cm is not None and self.length_cm <= Decimal("0.00"):
            raise ValidationError({"length_cm": "Length must be greater than 0 cm."})
        if self.width_cm is not None and self.width_cm <= Decimal("0.00"):
            raise ValidationError({"width_cm": "Width must be greater than 0 cm."})
        if self.height_cm is not None and self.height_cm <= Decimal("0.00"):
            raise ValidationError({"height_cm": "Height must be greater than 0 cm."})

    def can_transition_to(self, new_status):
        """
        Validates if transition from current status to new_status is allowed.
        """
        if self.status == new_status:
            return True
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def transition_to(self, new_status, location="", remarks="", raw_payload=None, save=True):
        """
        Executes a status transition, enforcing transition rules and recording
        an immutable entry in ShipmentStatusHistory.
        """
        if not self.can_transition_to(new_status):
            raise ValidationError(
                f"Invalid status transition from '{self.get_status_display()}' ({self.status}) to '{new_status}'."
            )

        self.status = new_status
        if save:
            self.save(update_fields=["status", "updated_at"])

        # Record immutable audit history
        ShipmentStatusHistory.objects.create(
            shipment=self,
            status=new_status,
            location=location,
            remarks=remarks,
            raw_payload=raw_payload,
        )
        return self


class ShipmentStatusHistory(models.Model):
    """
    Immutable audit log recording every status change of a Shipment.
    """
    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        related_name="status_history",
        help_text="The shipment being tracked."
    )
    status = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Status string at time of transition."
    )
    location = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Current physical location reported by courier."
    )
    remarks = models.TextField(
        blank=True,
        default="",
        help_text="Remarks or error messages associated with status change."
    )
    raw_payload = models.JSONField(
        blank=True,
        null=True,
        help_text="Full webhook/event payload associated with status update."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when status transition occurred."
    )

    class Meta:
        verbose_name = "Shipment Status History"
        verbose_name_plural = "Shipment Status Histories"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.shipment.vendor_order.vendor_order_number} -> {self.status} at {self.created_at.strftime('%Y-%m-%d %H:%M')}"

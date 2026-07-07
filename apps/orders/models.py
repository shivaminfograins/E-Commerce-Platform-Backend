"""
apps/orders/models.py
=====================
Production-level Order and OrderItem models.

Design decisions
----------------
* Order references Address with SET_NULL so that deleting a saved address
  does NOT cascade-delete the order history.  The denormalised address
  snapshot fields (full_name, phone, address_line_1 …) preserve the
  delivery address exactly as it existed at the moment of purchase —
  even if the user later edits or deletes their Address record.

* OrderItem references both Product and ProductVariant with SET_NULL so
  that deleting a catalogue entry does NOT cascade-delete order history.
  The snapshot fields (product_name, variant_name, sku, image, price)
  freeze the purchasable details at the time of purchase, making the
  order immutable and audit-safe regardless of future catalogue changes.

* order_number is a human-readable, unique identifier (e.g. "ORD-20260706-0001")
  generated in the overridden save() method.  It is indexed for fast lookup.

* All monetary fields use DecimalField (not FloatField) to avoid IEEE 754
  floating-point rounding errors in financial calculations.

* The total_amount on Order is denormalised for quick read performance.
  It must satisfy: total_amount = subtotal - discount + tax + shipping_charge.
  Enforcement lives in the serializer / service layer, not in the model,
  to keep model validation simple and testable.

* Enums (status, payment_method, payment_status) use string sentinels so
  that Django admin and DRF display human-readable labels automatically.

* Meta.indexes add a composite DB index on (user, status) and (created_at)
  to speed up the most common admin / customer dashboard queries.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import Address
from apps.products.models import Product, ProductVariant


# ===========================================================================
# ORDER
# ===========================================================================
class Order(models.Model):
    """
    Represents a single customer purchase.

    Lifecycle
    ---------
    pending → confirmed → packed → shipped → delivered
                                          ↘ cancelled (from any state before delivered)

    The address snapshot duplicates delivery info from the Address record so
    that historical orders remain accurate even after the user updates their
    address book.
    """

    # ───────────────────────────────────────────────────────────────────────
    # CHOICES — defined as class-level constants so other modules can import
    # them without instantiating the model (e.g. Order.PENDING).
    # ───────────────────────────────────────────────────────────────────────

    # Order lifecycle status
    PENDING   = "pending"
    CONFIRMED = "confirmed"
    PACKED    = "packed"
    SHIPPED   = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED  = "refunded"

    STATUS_CHOICES = [
        (PENDING,   "Pending"),    # Order placed; awaiting payment confirmation
        (CONFIRMED, "Confirmed"),  # Payment verified; queued for picking
        (PACKED,    "Packed"),     # Items picked & boxed; awaiting handover
        (SHIPPED,   "Shipped"),    # Handed to courier; tracking number assigned
        (DELIVERED, "Delivered"),  # Confirmed delivery to customer
        (CANCELLED, "Cancelled"),  # Cancelled by customer or system
        (REFUNDED,  "Refunded"),   # Cancellation acknowledged; refund issued
    ]

    # Payment gateway / method
    COD      = "cod"
    RAZORPAY = "razorpay"
    STRIPE   = "stripe"
    UPI      = "upi"

    PAYMENT_METHOD_CHOICES = [
        (COD,      "Cash On Delivery"),
        (RAZORPAY, "Razorpay"),
        (STRIPE,   "Stripe"),
        (UPI,      "UPI"),
    ]

    # Payment lifecycle status (independent of order status)
    PAYMENT_PENDING  = "pending"
    PAYMENT_PAID     = "paid"
    PAYMENT_FAILED   = "failed"
    PAYMENT_REFUNDED = "refunded"

    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_PENDING,  "Pending"),   # Awaiting payment
        (PAYMENT_PAID,     "Paid"),      # Payment captured successfully
        (PAYMENT_FAILED,   "Failed"),    # Gateway declined / timed out
        (PAYMENT_REFUNDED, "Refunded"),  # Amount returned to customer
    ]

    # ───────────────────────────────────────────────────────────────────────
    # RELATIONSHIPS
    # ───────────────────────────────────────────────────────────────────────

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        # CASCADE: deleting a user account removes all their orders.
        # This is appropriate for GDPR "right to erasure" flows — the
        # alternative (SET_NULL) would leave orphaned financial records.
        related_name="orders",
        db_index=True,
        help_text="The authenticated customer who placed this order.",
    )

    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        # SET_NULL: the address book entry may be deleted by the user
        # without destroying the order.  The snapshot fields below retain
        # the delivery address permanently.
        null=True,
        blank=True,
        related_name="orders",
        help_text=(
            "Reference to the Address record selected at checkout. "
            "May become NULL if the user deletes the address later. "
            "Use the snapshot fields for the authoritative delivery address."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # ORDER IDENTITY
    # ───────────────────────────────────────────────────────────────────────

    order_number = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        editable=False,
        help_text=(
            "Human-readable, globally unique order identifier. "
            "Auto-generated on first save in the format "
            "'ORD-YYYYMMDD-<UUID4_short>'. "
            "Never reused, never editable after creation."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # STATUS FIELDS
    # ───────────────────────────────────────────────────────────────────────

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True,
        help_text=(
            "Current fulfilment state of the order. "
            "Transitions: pending → confirmed → packed → shipped → delivered. "
            "Cancellation is allowed from any pre-delivered state."
        ),
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default=COD,
        help_text=(
            "The payment instrument chosen by the customer at checkout. "
            "For gateway-based methods (Razorpay, Stripe) the payment_id "
            "from the gateway transaction is stored in the linked Payment record."
        ),
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_PENDING,
        db_index=True,
        help_text=(
            "Current payment lifecycle state, maintained by the payments app "
            "webhook handler.  Decoupled from order status so that an order "
            "can be 'shipped' while its payment is still 'pending' (e.g. COD)."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # ADDRESS SNAPSHOT
    # Denormalised copy of the delivery address captured at checkout.
    # These fields are immutable after order creation.
    # ───────────────────────────────────────────────────────────────────────

    snapshot_full_name = models.CharField(
        max_length=100,
        help_text="Recipient full name as entered at checkout.",
    )

    snapshot_phone = models.CharField(
        max_length=20,
        help_text="Contact phone number for delivery queries.",
    )

    snapshot_address_line_1 = models.CharField(
        max_length=255,
        help_text="Primary address line (house/flat/building number and street).",
    )

    snapshot_address_line_2 = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Secondary address line (apartment, suite, wing — optional).",
    )

    snapshot_landmark = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Nearby landmark to assist the courier (optional).",
    )

    snapshot_city = models.CharField(
        max_length=100,
        help_text="City / district of the delivery address.",
    )

    snapshot_state = models.CharField(
        max_length=100,
        help_text="State / province of the delivery address.",
    )

    snapshot_country = models.CharField(
        max_length=100,
        default="India",
        help_text="Country of the delivery address.",
    )

    snapshot_postal_code = models.CharField(
        max_length=20,
        help_text="PIN / ZIP / postal code.",
    )

    # ───────────────────────────────────────────────────────────────────────
    # PRICE BREAKDOWN
    # All values in the store's base currency (INR by default).
    # max_digits=12 supports up to ₹9,999,999,999.99 (~10 billion) per order.
    # ───────────────────────────────────────────────────────────────────────

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Sum of (price × quantity) for all OrderItems before any "
            "discounts, tax, or shipping charges are applied."
        ),
    )

    shipping_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Flat or calculated shipping fee added to the order. "
            "Zero for free-shipping promotions or digital products."
        ),
    )

    discount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Total monetary discount applied to the order "
            "(coupon, loyalty points, promotional code, etc.). "
            "Stored as a positive value; subtracted during total calculation."
        ),
    )

    tax = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Total tax amount (GST / VAT) applied to the order. "
            "Derived from the tax rate configured in settings; "
            "stored denormalised for invoice / reporting accuracy."
        ),
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Grand total charged to the customer. "
            "Formula: subtotal - discount + tax + shipping_charge. "
            "Denormalised here for fast display; recomputed by the "
            "service layer on every order write."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # MISC
    # ───────────────────────────────────────────────────────────────────────

    coupon_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=(
            "Snapshot of the coupon code applied at checkout, if any. "
            "Stored for audit and customer-service reference."
        ),
    )

    notes = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Free-text notes from the customer (e.g. delivery instructions) "
            "or internal staff remarks (e.g. 'fragile, handle with care')."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # TIMESTAMPS
    # ───────────────────────────────────────────────────────────────────────

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="UTC timestamp when the order was first created.",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="UTC timestamp of the most recent change to this record.",
    )

    # ───────────────────────────────────────────────────────────────────────
    # META
    # ───────────────────────────────────────────────────────────────────────

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        indexes = [
            # Customer dashboard: "my orders" filtered by status
            models.Index(fields=["user", "status"], name="idx_order_user_status"),
            # Admin date-range reports
            models.Index(fields=["created_at"], name="idx_order_created_at"),
            # Payment reconciliation queries
            models.Index(
                fields=["payment_status", "payment_method"],
                name="idx_order_payment",
            ),
        ]

    # ───────────────────────────────────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────────────────────────────────

    def _generate_order_number(self):
        """
        Generate a collision-resistant, human-readable order number.

        Format: ORD-YYYYMMDD-<8-char UUID4 prefix>
        Example: ORD-20260706-A1B2C3D4

        The UUID suffix makes the number globally unique without requiring a
        database round-trip for a sequence number, which avoids race conditions
        under high concurrency.
        """
        date_str  = timezone.now().strftime("%Y%m%d")
        uid_chunk = uuid.uuid4().hex[:8].upper()
        return f"ORD-{date_str}-{uid_chunk}"

    def save(self, *args, **kwargs):
        """
        Auto-assign order_number on first creation only.
        Recompute total_amount to keep it consistent with the breakdown fields.
        """
        if not self.order_number:
            self.order_number = self._generate_order_number()

        # Guard against accidental negative totals
        self.discount = max(Decimal("0.00"), self.discount)

        # Recompute grand total
        self.total_amount = (
            self.subtotal
            + self.shipping_charge
            + self.tax
            - self.discount
        )

        super().save(*args, **kwargs)

    @property
    def formatted_delivery_address(self):
        """
        Return a single multi-line string of the snapshot delivery address,
        suitable for display on invoices and packing slips.
        """
        parts = [
            self.snapshot_full_name,
            self.snapshot_address_line_1,
        ]
        if self.snapshot_address_line_2:
            parts.append(self.snapshot_address_line_2)
        if self.snapshot_landmark:
            parts.append(f"Near: {self.snapshot_landmark}")
        parts.append(
            f"{self.snapshot_city}, {self.snapshot_state} - {self.snapshot_postal_code}"
        )
        parts.append(self.snapshot_country)
        return "\n".join(parts)

    @property
    def item_count(self):
        """Total number of distinct line items on this order."""
        return self.items.count()

    @property
    def is_cancellable(self):
        """
        An order can be cancelled only before it is delivered.
        Already-cancelled or refunded orders are also excluded.
        """
        return self.status not in (
            self.DELIVERED,
            self.CANCELLED,
            self.REFUNDED,
        )

    def __str__(self):
        return f"{self.order_number} ({self.get_status_display()})"


# ===========================================================================
# ORDER ITEM
# ===========================================================================
class OrderItem(models.Model):
    """
    A single line on an order — one (product, variant) pair with quantity.

    Snapshot strategy
    -----------------
    The live FKs (product, variant) are kept as nullable SET_NULL references
    so that admins can still navigate from an order item back to the current
    catalogue entry when it still exists.  However, ALL customer-facing and
    audit-critical data is duplicated in the snapshot fields so that:

      1. Deleting or editing a Product/ProductVariant never mutates order history.
      2. Price at time of purchase is preserved for accurate refund calculations.
      3. Invoices and packing slips render correctly even for discontinued SKUs.
    """

    # ───────────────────────────────────────────────────────────────────────
    # RELATIONSHIPS
    # ───────────────────────────────────────────────────────────────────────

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        # CASCADE: order items are meaningless without their parent order.
        related_name="items",
        help_text="The parent order this line item belongs to.",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        # SET_NULL: preserves the order item row even if the product is
        # removed from the catalogue.  Use snapshot fields for display.
        null=True,
        blank=True,
        related_name="order_items",
        help_text=(
            "Live reference to the Product in the catalogue. "
            "May be NULL if the product was deleted after purchase. "
            "Always use snapshot fields for financial / display logic."
        ),
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        # SET_NULL: same rationale as product above.
        null=True,
        blank=True,
        related_name="order_items",
        help_text=(
            "Live reference to the specific ProductVariant purchased "
            "(carries price, SKU, and stock at purchase time). "
            "May be NULL if the variant was deleted after purchase."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # PRODUCT SNAPSHOT
    # Immutable copy of catalogue data captured at the moment of checkout.
    # ───────────────────────────────────────────────────────────────────────

    product_name = models.CharField(
        max_length=255,
        help_text=(
            "Snapshot: product name at the time of purchase "
            "(e.g. 'iPhone 15 Pro')."
        ),
    )

    variant_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Snapshot: variant label at the time of purchase "
            "(e.g. '256GB / Space Black'). "
            "Empty for products with no variants."
        ),
    )

    sku = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Snapshot: Stock-Keeping Unit of the variant at the time of purchase. "
            "Indexed to support warehouse / fulfilment queries by SKU."
        ),
    )

    product_image = models.URLField(
        max_length=1000,
        blank=True,
        default="",
        help_text=(
            "Snapshot: absolute URL of the primary product image at checkout. "
            "Stored as a URL (not an ImageField) because the physical file may "
            "be replaced or deleted, but order history should always show the "
            "image the customer saw when they purchased."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # PRICE & QUANTITY
    # ───────────────────────────────────────────────────────────────────────

    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "Snapshot: unit price of the variant at the time of purchase "
            "(after any per-item discount but before order-level discount). "
            "This is the price-per-unit that will appear on invoices."
        ),
    )

    quantity = models.PositiveIntegerField(
        help_text=(
            "Number of units of this variant ordered. "
            "Always ≥ 1; validated by the serializer."
        ),
    )

    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "Denormalised line total: price × quantity. "
            "Stored to avoid recomputing on every read and to lock in the "
            "value even if price logic changes in the future."
        ),
    )

    # ───────────────────────────────────────────────────────────────────────
    # META
    # ───────────────────────────────────────────────────────────────────────

    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        # Composite index used by warehouse / packing-slip queries
        indexes = [
            models.Index(fields=["order", "sku"], name="idx_orderitem_order_sku"),
        ]

    # ───────────────────────────────────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────────────────────────────────

    def save(self, *args, **kwargs):
        """
        Auto-compute the line total from price × quantity before saving.
        This ensures consistency even when the item is created or updated
        programmatically without explicitly setting `total`.
        """
        self.total = self.price * self.quantity
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        """
        Human-readable label combining product and variant names.
        Used on invoices, packing slips, and admin list views.
        """
        if self.variant_name:
            return f"{self.product_name} — {self.variant_name}"
        return self.product_name

    def __str__(self):
        return (
            f"{self.order.order_number}  ·  "
            f"{self.display_name}  ×  {self.quantity}"
        )
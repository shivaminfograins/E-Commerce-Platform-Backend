"""
apps/orders/admin.py
====================
Django Admin registration for Order and OrderItem.

Design goals
------------
* Admins can see and manage the full order lifecycle without touching the
  database directly.
* The OrderItem inline is TabularInline so multiple items are shown in a
  compact table within the Order change page.
* All snapshot fields are read-only in the admin — they must never be
  edited after placement (they are the legal record of what the customer
  purchased and at what price).
* Custom list filters and search fields allow quick lookups for
  customer-service workflows (find by order number, user email, SKU).
* Custom coloured status badges in the list view give at-a-glance insight.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import Order, OrderItem


# ===========================================================================
# HELPERS
# ===========================================================================

# Status → (background-color, text-color) for the coloured badge in list view
_STATUS_COLORS = {
    Order.PENDING:   ("#fef3c7", "#b45309"),   # amber
    Order.CONFIRMED: ("#dbeafe", "#1d4ed8"),   # blue
    Order.PACKED:    ("#ede9fe", "#6d28d9"),   # violet
    Order.SHIPPED:   ("#e0f2fe", "#0369a1"),   # sky
    Order.DELIVERED: ("#dcfce7", "#15803d"),   # green
    Order.CANCELLED: ("#fee2e2", "#b91c1c"),   # red
    Order.REFUNDED:  ("#fce7f3", "#9d174d"),   # pink
}

_PAYMENT_STATUS_COLORS = {
    Order.PAYMENT_PENDING:  ("#fef3c7", "#b45309"),
    Order.PAYMENT_PAID:     ("#dcfce7", "#15803d"),
    Order.PAYMENT_FAILED:   ("#fee2e2", "#b91c1c"),
    Order.PAYMENT_REFUNDED: ("#fce7f3", "#9d174d"),
}


# ===========================================================================
# ORDER ITEM INLINE
# ===========================================================================
class OrderItemInline(admin.TabularInline):
    """
    Shows all line items for an order in a compact tabular layout
    directly inside the Order change page.

    All fields are read-only because order items must not be altered after
    placement (they constitute the legal purchase record).
    """
    model = OrderItem
    extra = 0          # no blank rows
    can_delete = False # items cannot be removed from an existing order via admin
    show_change_link = True  # link to the OrderItem's own change page

    # Columns shown in the inline table
    readonly_fields = (
        "display_name_col",
        "sku",
        "price",
        "quantity",
        "total",
        "product",
        "variant",
        "product_image",
    )

    # Suppress the fields that appear as raw inline inputs
    fields = readonly_fields

    @admin.display(description="Item")
    def display_name_col(self, obj):
        """Combine product + variant name for the inline table."""
        return obj.display_name


# ===========================================================================
# ORDER ADMIN
# ===========================================================================
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Full Order management interface.

    List view features
    ------------------
    • Coloured status and payment-status badges for at-a-glance insight.
    • Search by order number, user email/username, SKU on an item.
    • Date hierarchy for filtering by month/day.
    • Sidebar filters for status, payment_status, and payment_method.

    Detail view features
    --------------------
    • Financial breakdown (subtotal, discount, tax, shipping, total) in a
      dedicated fieldset.
    • Address snapshot in a dedicated read-only fieldset.
    • OrderItemInline shows all purchased items.
    • order_number is read-only (generated on creation; must not be edited).
    """

    # ── List view ─────────────────────────────────────────────────────────
    list_display = (
        "order_number",
        "user",
        "coloured_status",
        "coloured_payment_status",
        "payment_method",
        "item_count_col",
        "total_amount",
        "created_at",
    )
    list_filter  = ("status", "payment_status", "payment_method", "created_at")
    search_fields = (
        "order_number",
        "user__email",
        "user__username",
        "snapshot_full_name",
        "snapshot_phone",
        "items__sku",            # search by SKU on a line item
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # ── Detail view ───────────────────────────────────────────────────────
    readonly_fields = (
        "order_number",          # auto-generated; immutable
        "formatted_delivery_address",
        "created_at",
        "updated_at",
        "total_amount",          # recomputed in save(); show but don't allow manual edit
    )

    fieldsets = (
        # ── Identity & Status ──────────────────────────────────────────
        ("Order Identity", {
            "fields": (
                "order_number",
                "user",
                "status",
                "notes",
                "coupon_code",
                ("created_at", "updated_at"),
            ),
        }),

        # ── Payment ────────────────────────────────────────────────────
        ("Payment", {
            "fields": (
                "payment_method",
                "payment_status",
            ),
        }),

        # ── Financial Breakdown ────────────────────────────────────────
        ("Price Breakdown", {
            "fields": (
                "subtotal",
                "shipping_charge",
                "discount",
                "tax",
                "total_amount",     # read-only; recomputed by save()
            ),
            "description": (
                "grand total = subtotal − discount + tax + shipping_charge"
            ),
        }),

        # ── Delivery Address (live FK) ─────────────────────────────────
        ("Delivery Address — Live Reference", {
            "fields": ("address",),
            "description": (
                "Live FK to the Address record. May be NULL if the user deleted "
                "their address. Always use the snapshot below for legal records."
            ),
            "classes": ("collapse",),
        }),

        # ── Address Snapshot ───────────────────────────────────────────
        ("Delivery Address — Snapshot (immutable)", {
            "fields": (
                "formatted_delivery_address",   # computed property for display
                "snapshot_full_name",
                "snapshot_phone",
                "snapshot_address_line_1",
                "snapshot_address_line_2",
                "snapshot_landmark",
                "snapshot_city",
                "snapshot_state",
                "snapshot_postal_code",
                "snapshot_country",
            ),
            "description": (
                "Exact delivery address captured at checkout. "
                "These fields are the authoritative legal record and "
                "must NOT be edited after order placement."
            ),
            "classes": ("collapse",),
        }),
    )

    inlines = [OrderItemInline]

    # ── Custom list columns ───────────────────────────────────────────────

    @admin.display(description="Status", ordering="status")
    def coloured_status(self, obj):
        bg, fg = _STATUS_COLORS.get(obj.status, ("#e2e8f0", "#334155"))
        return format_html(
            '<span style="background:{};color:{};padding:2px 10px;'
            'border-radius:99px;font-size:0.75rem;font-weight:600;">'
            "{}</span>",
            bg, fg, obj.get_status_display(),
        )

    @admin.display(description="Payment", ordering="payment_status")
    def coloured_payment_status(self, obj):
        bg, fg = _PAYMENT_STATUS_COLORS.get(obj.payment_status, ("#e2e8f0", "#334155"))
        return format_html(
            '<span style="background:{};color:{};padding:2px 10px;'
            'border-radius:99px;font-size:0.75rem;font-weight:600;">'
            "{}</span>",
            bg, fg, obj.get_payment_status_display(),
        )

    @admin.display(description="Items")
    def item_count_col(self, obj):
        """Number of distinct line items on this order."""
        return obj.items.count()

    # ── Queryset optimisation ─────────────────────────────────────────────
    def get_queryset(self, request):
        """
        Prefetch related items and select user + address in a single query
        to avoid N+1 on the list page.
        """
        return (
            super()
            .get_queryset(request)
            .select_related("user", "address")
            .prefetch_related("items")
        )


# ===========================================================================
# ORDER ITEM ADMIN (standalone)
# ===========================================================================
@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """
    Standalone OrderItem admin — useful for warehouse / fulfilment staff
    who need to search by SKU across all orders.

    All snapshot fields are read-only (immutable legal record).
    """

    list_display = (
        "order",
        "display_name_col",
        "sku",
        "price",
        "quantity",
        "total",
        "product",
        "variant",
    )
    list_filter  = ("order__status", "order__payment_status")
    search_fields = (
        "order__order_number",
        "product_name",
        "variant_name",
        "sku",
        "order__user__email",
    )
    ordering = ("-order__created_at",)

    # All snapshot + computed fields are read-only
    readonly_fields = (
        "display_name_col",
        "total",            # auto-computed in save()
        "order",            # parent order must not be changed
        "product",
        "variant",
        "product_name",
        "variant_name",
        "sku",
        "product_image",
        "price",
        "quantity",
    )

    fieldsets = (
        ("Line Item — Snapshot (read-only)", {
            "fields": (
                "order",
                "product_name",
                "variant_name",
                "sku",
                "product_image",
                "price",
                "quantity",
                "total",
            ),
        }),
        ("Live Catalogue References", {
            "fields": ("product", "variant"),
            "description": (
                "These may be NULL if the product / variant was deleted "
                "from the catalogue after the order was placed."
            ),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Item")
    def display_name_col(self, obj):
        return obj.display_name

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("order", "order__user", "product", "variant")
        )

    def has_add_permission(self, request):
        """
        Order items should only be created through the checkout flow,
        not manually via the admin, to maintain stock-decrement integrity.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Deleting line items would corrupt order totals and audit trails.
        Only superusers may do this, and only through the database.
        """
        return request.user.is_superuser

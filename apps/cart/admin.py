from django.contrib import admin
from .models import Guest, CartItem


# ----------------------------------------------------------
# Inline: show cart items directly inside the Guest detail page.
class CartItemInline(admin.TabularInline):
    model = CartItem
    # `variant` is the new FK; quantity and timestamps are useful context.
    fields = ("variant", "quantity", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    # Avoid accidentally creating blank rows in the inline form.
    extra = 0
    # Allow admins to remove individual items from within the guest page.
    can_delete = True
    show_change_link = True   # links directly to the CartItem change form


# ----------------------------------------------------------
# Guest Admin
@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ("id", "guest_token_short", "created_at", "cart_item_count")
    search_fields = ("guest_token",)
    readonly_fields = ("guest_token", "created_at")
    ordering = ("-created_at",)
    inlines = [CartItemInline]

    @admin.display(description="Token (preview)")
    def guest_token_short(self, obj):
        """Show only the first 8 characters so the list stays readable."""
        return obj.guest_token[:8]

    @admin.display(description="# Items")
    def cart_item_count(self, obj):
        """Quick count of how many variants the guest has in their cart."""
        return obj.cart_items.count()


# ----------------------------------------------------------
# CartItem Admin
@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    # ── List view ─────────────────────────────────────────────
    list_display = (
        "id",
        "owner_label",      # "user: alice" or "guest: abc12345"
        "variant",          # calls ProductVariant.__str__()
        "variant_sku",      # handy for stock look-ups
        "quantity",
        "created_at",
        "updated_at",
    )
    list_filter = ("created_at", "updated_at")
    search_fields = (
        "user__username",
        "user__email",
        "guest__guest_token",
        "variant__sku",
        "variant__name",
        "variant__product__name",
    )
    ordering = ("-created_at",)

    # ── Detail / change view ──────────────────────────────────
    # `variant` replaces the old `product` field everywhere.
    fields = ("user", "guest", "variant", "quantity", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    # Raw-id widgets keep the dropdowns fast when the tables are large.
    raw_id_fields = ("user", "guest", "variant")

    # ── Custom columns ────────────────────────────────────────
    @admin.display(description="Owner")
    def owner_label(self, obj):
        """
        Human-readable owner: shows the username for authenticated users,
        or an abbreviated guest token for anonymous shoppers.
        """
        if obj.user_id:
            return f"user: {obj.user.username}"
        return f"guest: {obj.guest.guest_token[:8]}"

    @admin.display(description="SKU")
    def variant_sku(self, obj):
        """Surface the variant SKU in the list view for quick identification."""
        return obj.variant.sku

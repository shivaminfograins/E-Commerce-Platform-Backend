# Migration: cart refactor — Product → ProductVariant
#
# What this migration does, step by step:
#
#   1. Clears ALL existing CartItem rows.
#      Rationale: the old `product` FK pointed to `products.Product`; that
#      reference is no longer meaningful after the swap.  Since cart data is
#      ephemeral (a shopper can just re-add items), it is safe to wipe it
#      rather than attempt a lossy data migration.
#
#   2. Removes the old `product` ForeignKey column.
#
#   3. Adds the new `variant` ForeignKey (non-nullable, CASCADE) pointing
#      to `products.ProductVariant`.  This is safe now because the table is
#      empty.
#
#   4. Adds two partial UniqueConstraints so the same variant cannot appear
#      twice in one owner's cart:
#        • unique_user_variant  (applies when user IS set)
#        • unique_guest_variant (applies when guest IS set)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def clear_cart_items(apps, schema_editor):
    """
    Delete all CartItem rows before we swap the FK.
    The old product references become stale after this migration, so there
    is nothing worth preserving.
    """
    CartItem = apps.get_model("cart", "CartItem")
    CartItem.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        # The previous cart migration that created Guest + CartItem tables.
        ("cart", "0001_initial"),
        # ProductVariant lives here; adjust to the latest products migration
        # number if your project has gone further than 0002.
        ("products", "0002_categoryimage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Step 1: wipe stale cart data ──────────────────────
        # Run the Python helper above so existing rows don't violate the
        # NOT NULL constraint introduced in step 3.
        migrations.RunPython(
            clear_cart_items,
            reverse_code=migrations.RunPython.noop,  # no meaningful rollback
        ),

        # ── Step 2: drop the old product FK column ────────────
        migrations.RemoveField(
            model_name="cartitem",
            name="product",
        ),

        # ── Step 3: add the new variant FK column ─────────────
        # Non-nullable because every cart item MUST belong to a specific
        # purchasable variant (price / SKU / stock live on the variant).
        migrations.AddField(
            model_name="cartitem",
            name="variant",
            field=models.ForeignKey(
                help_text="The specific product variant (size, colour, etc.) added to the cart.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cart_items",
                to="products.productvariant",
            ),
            preserve_default=False,
        ),

        # ── Step 4a: UniqueConstraint for authenticated users ──
        # Prevents a user from having two rows for the same variant;
        # the view/serializer layer should increment quantity instead.
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=["user", "variant"],
                name="unique_user_variant",
            ),
        ),

        # ── Step 4b: UniqueConstraint for guest shoppers ───────
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(guest__isnull=False),
                fields=["guest", "variant"],
                name="unique_guest_variant",
            ),
        ),
    ]

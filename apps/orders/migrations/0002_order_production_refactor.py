# Migration 0002: Order model production refactor
#
# What changed from 0001 (and why):
#
# ORDER MODEL
# -----------
#   Renamed address snapshot fields:
#     full_name       → snapshot_full_name
#     phone           → snapshot_phone
#     address_line_1  → snapshot_address_line_1
#     address_line_2  → snapshot_address_line_2
#     landmark        → snapshot_landmark
#     city            → snapshot_city
#     state           → snapshot_state
#     country         → snapshot_country
#     postal_code     → snapshot_postal_code
#
#   Rationale: the snapshot_ prefix makes it clear in every query, admin,
#   and serializer that these fields are frozen copies, NOT live data pulled
#   from the Address FK.
#
#   New fields added:
#     coupon_code     - snapshot of the applied coupon code (blank by default)
#     refunded status - added to STATUS_CHOICES and PAYMENT_STATUS_CHOICES
#                       (choice list changes are schema-free; no DB column added)
#
#   Field widths increased:
#     order_number:   max_length 30 → 40  (supports the ORD-YYYYMMDD-UUID8 format)
#     snapshot_phone: max_length 15 → 20  (E.164 numbers can be up to 15 digits +
#                                          international prefix punctuation)
#
#   Indexes added:
#     idx_order_user_status  on (user, status)
#     idx_order_created_at   on (created_at)
#     idx_order_payment      on (payment_status, payment_method)
#
# ORDER ITEM MODEL
# ----------------
#   New fields added:
#     variant         - ForeignKey to ProductVariant (SET_NULL; nullable)
#     variant_name    - snapshot of variant label at purchase time
#
#   Renamed fields:
#     product_name (unchanged)
#     product_sku  → sku  (shorter, consistent with ProductVariant.sku)
#     product_image: CharField(500) → URLField(1000)
#
#   Indexes added:
#     idx_orderitem_order_sku on (order, sku)
#
# DATA STRATEGY
# -------------
#   There are 0 existing Order / OrderItem rows (verified).
#   We therefore DROP and RECREATE the tables cleanly rather than
#   applying destructive ALTER + RENAME chains, which simplifies the
#   migration and avoids SQLite column-rename limitations.

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
        ("accounts", "0002_alter_address_options_address_address_type_and_more"),
        ("products", "0002_categoryimage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── Step 1: Drop old tables (0 rows; safe) ──────────────────────
        migrations.DeleteModel(name="OrderItem"),
        migrations.DeleteModel(name="Order"),

        # ── Step 2: Recreate Order with full production schema ──────────
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),

                # ── Relationships ──────────────────────────────────────
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="orders",
                    to=settings.AUTH_USER_MODEL,
                    db_index=True,
                    help_text="The authenticated customer who placed this order.",
                )),
                ("address", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="orders",
                    to="accounts.address",
                    help_text="Live FK to the Address record; may become NULL.",
                )),

                # ── Order identity ─────────────────────────────────────
                ("order_number", models.CharField(
                    max_length=40, unique=True, db_index=True, editable=False,
                    help_text="Human-readable unique order ID, auto-generated.",
                )),

                # ── Status ─────────────────────────────────────────────
                ("status", models.CharField(
                    max_length=20,
                    choices=[
                        ("pending",   "Pending"),
                        ("confirmed", "Confirmed"),
                        ("packed",    "Packed"),
                        ("shipped",   "Shipped"),
                        ("delivered", "Delivered"),
                        ("cancelled", "Cancelled"),
                        ("refunded",  "Refunded"),
                    ],
                    default="pending",
                    db_index=True,
                )),
                ("payment_method", models.CharField(
                    max_length=20,
                    choices=[
                        ("cod",      "Cash On Delivery"),
                        ("razorpay", "Razorpay"),
                        ("stripe",   "Stripe"),
                        ("upi",      "UPI"),
                    ],
                    default="cod",
                )),
                ("payment_status", models.CharField(
                    max_length=20,
                    choices=[
                        ("pending",  "Pending"),
                        ("paid",     "Paid"),
                        ("failed",   "Failed"),
                        ("refunded", "Refunded"),
                    ],
                    default="pending",
                    db_index=True,
                )),

                # ── Address snapshot ───────────────────────────────────
                ("snapshot_full_name",      models.CharField(max_length=100)),
                ("snapshot_phone",          models.CharField(max_length=20)),
                ("snapshot_address_line_1", models.CharField(max_length=255)),
                ("snapshot_address_line_2", models.CharField(max_length=255, blank=True, default="")),
                ("snapshot_landmark",       models.CharField(max_length=255, blank=True, default="")),
                ("snapshot_city",           models.CharField(max_length=100)),
                ("snapshot_state",          models.CharField(max_length=100)),
                ("snapshot_country",        models.CharField(max_length=100, default="India")),
                ("snapshot_postal_code",    models.CharField(max_length=20)),

                # ── Price breakdown ────────────────────────────────────
                ("subtotal",       models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))),
                ("shipping_charge",models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))),
                ("discount",       models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))),
                ("tax",            models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))),
                ("total_amount",   models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))),

                # ── Misc ───────────────────────────────────────────────
                ("coupon_code", models.CharField(max_length=50, blank=True, default="")),
                ("notes",       models.TextField(blank=True, default="")),

                # ── Timestamps ─────────────────────────────────────────
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Order",
                "verbose_name_plural": "Orders",
            },
        ),

        # ── Step 3: Add composite DB indexes to Order ───────────────────
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["user", "status"],
                name="idx_order_user_status",
            ),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["created_at"],
                name="idx_order_created_at",
            ),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["payment_status", "payment_method"],
                name="idx_order_payment",
            ),
        ),

        # ── Step 4: Recreate OrderItem with variant support ─────────────
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),

                # ── Relationships ──────────────────────────────────────
                ("order", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="items",
                    to="orders.order",
                    help_text="Parent order.",
                )),
                ("product", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="order_items",
                    to="products.product",
                    help_text="Live FK to Product; may be NULL if product deleted.",
                )),
                ("variant", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="order_items",
                    to="products.productvariant",
                    help_text="Live FK to ProductVariant; may be NULL if variant deleted.",
                )),

                # ── Snapshot ───────────────────────────────────────────
                ("product_name",  models.CharField(max_length=255,  help_text="Snapshot: product name at time of purchase.")),
                ("variant_name",  models.CharField(max_length=100,  blank=True, default="", help_text="Snapshot: variant label.")),
                ("sku",           models.CharField(max_length=100,  blank=True, default="", db_index=True, help_text="Snapshot: variant SKU.")),
                ("product_image", models.URLField(max_length=1000,  blank=True, default="", help_text="Snapshot: absolute URL of primary product image.")),

                # ── Price & quantity ────────────────────────────────────
                ("price",    models.DecimalField(max_digits=12, decimal_places=2, help_text="Snapshot: unit price at time of purchase.")),
                ("quantity", models.PositiveIntegerField(help_text="Units ordered (≥ 1).")),
                ("total",    models.DecimalField(max_digits=12, decimal_places=2, help_text="price × quantity (auto-computed in save()).")),
            ],
            options={
                "verbose_name": "Order Item",
                "verbose_name_plural": "Order Items",
            },
        ),

        # ── Step 5: Add composite DB index to OrderItem ─────────────────
        migrations.AddIndex(
            model_name="orderitem",
            index=models.Index(
                fields=["order", "sku"],
                name="idx_orderitem_order_sku",
            ),
        ),
    ]

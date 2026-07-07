"""
apps/orders/urls.py
===================

URL patterns for the Orders app.
Registered under "api/orders/" in config/urls.py.

Full URL table
--------------
  POST   /api/orders/               → PlaceOrderView   place a new order
  GET    /api/orders/               → OrderListView    paginated order list
  GET    /api/orders/<pk>/          → OrderDetailView  full order detail
  PATCH  /api/orders/<pk>/cancel/   → CancelOrderView  cancel an order

Design notes
------------
• POST and GET share the same /api/orders/ path but are handled by
  separate view classes.  This keeps each class focused on a single
  responsibility and makes permission checks, serialisers, and querysets
  independently testable.

• The cancel route uses PATCH (not POST or DELETE) because it partially
  updates the `status` field of an existing resource.  The record itself
  is preserved for audit history.

• <int:pk> enforces that the PK is a positive integer at the URL level —
  non-numeric PKs return 404 without ever reaching the view.

• app_name = "orders" enables namespace-safe reverse lookups:
    reverse("orders:place-order")
    reverse("orders:order-list")
    reverse("orders:order-detail", kwargs={"pk": 42})
    reverse("orders:order-cancel", kwargs={"pk": 42})
"""

from django.urls import path

from .views import (
    CancelOrderView,
    OrderDetailView,
    OrderListView,
    PlaceOrderView,
)

app_name = "orders"

urlpatterns = [

    # ── /api/orders/  ─────────────────────────────────────────────────────
    path(
        "",
        PlaceOrderView.as_view(),   # POST — place a new order
        name="place-order",
    ),
    path(
        "list/",
        OrderListView.as_view(),    # GET  — paginated + search + sort
        name="order-list",
    ),

    # ── /api/orders/<pk>/  ────────────────────────────────────────────────
    path(
        "<int:pk>/",
        OrderDetailView.as_view(),  # GET  — full detail with items
        name="order-detail",
    ),
    path(
        "<int:pk>/cancel/",
        CancelOrderView.as_view(),  # PATCH — cancel + stock restore
        name="order-cancel",
    ),
]

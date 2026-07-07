from django.urls import path
from .views import (
    CartAPIView,
    CartItemDetailAPIView,
    CartClearAPIView,
    CartMergeAPIView,
)

# Cart URL patterns
# ─────────────────────────────────────────────────────────────
#  GET    /cart/                   → list cart items
#  POST   /cart/                   → add variant to cart
#  DELETE /cart/clear/             → wipe entire cart
#  POST   /cart/merge/             → merge guest cart into user cart (JWT required)
#  PATCH  /cart/<variant_id>/      → set quantity of a specific item
#  DELETE /cart/<variant_id>/      → remove a specific item
# ─────────────────────────────────────────────────────────────
# NOTE: "clear/" and "merge/" are placed BEFORE "<int:variant_id>/"
# so Django doesn't try to resolve the literal strings "clear" or
# "merge" as integers.
# ─────────────────────────────────────────────────────────────

urlpatterns = [
    path("", CartAPIView.as_view(), name="cart-list-create"),
    path("clear/", CartClearAPIView.as_view(), name="cart-clear"),
    path("merge/", CartMergeAPIView.as_view(), name="cart-merge"),
    path("<int:variant_id>/", CartItemDetailAPIView.as_view(), name="cart-item-detail"),
]

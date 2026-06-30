from django.urls import path
from .views import WishlistListCreateAPIView, WishlistItemDeleteAPIView

urlpatterns = [
    path("", WishlistListCreateAPIView.as_view(), name="wishlist-list-create"),
    path("<int:product_id>/", WishlistItemDeleteAPIView.as_view(), name="wishlist-delete"),
]

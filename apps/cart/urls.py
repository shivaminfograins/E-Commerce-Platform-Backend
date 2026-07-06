from django.urls import path
from .views import CartAPIView, CartItemDetailAPIView, CartMergeAPIView

urlpatterns = [
    path("", CartAPIView.as_view(), name="cart-list-create"),
    path("<int:product_id>/", CartItemDetailAPIView.as_view(), name="cart-detail"),
    path("merge/", CartMergeAPIView.as_view(), name="cart-merge"),
]

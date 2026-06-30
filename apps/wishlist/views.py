from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListCreateAPIView
from .models import WishlistItem
from .serializers import WishlistItemSerializer, WishlistItemCreateSerializer

class WishlistListCreateAPIView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WishlistItem.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return WishlistItemCreateSerializer
        return WishlistItemSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=request.user)
        # Return full details using read serializer
        read_serializer = WishlistItemSerializer(instance)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

class WishlistItemDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, product_id):
        user = request.user
        try:
            item = WishlistItem.objects.get(user=user, product_id=product_id)
            item.delete()
            return Response({"message": "Product removed from wishlist."}, status=status.HTTP_200_OK)
        except WishlistItem.DoesNotExist:
            return Response({"detail": "Wishlist item not found."}, status=status.HTTP_404_NOT_FOUND)

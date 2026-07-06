import uuid
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import CartItem, Guest
from .serializers import CartItemSerializer, CartItemCreateUpdateSerializer
from apps.products.models import Product

def get_or_create_guest(request):
    """
    Get or create a Guest instance based on cookie or custom header.
    """
    guest_token = request.headers.get("X-Guest-ID") or request.COOKIES.get("guest_id")
    guest = None
    if guest_token:
        try:
            guest = Guest.objects.get(guest_token=guest_token)
        except Guest.DoesNotExist:
            pass
            
    if not guest:
        guest_token = str(uuid.uuid4())
        guest = Guest.objects.create(guest_token=guest_token)
        
    return guest, guest_token

def merge_guest_cart(user, guest_token):
    """
    Merge items from guest cart into authenticated user cart.
    """
    if not guest_token:
        return
    try:
        guest = Guest.objects.get(guest_token=guest_token)
        guest_items = CartItem.objects.filter(guest=guest)
        for item in guest_items:
            user_item, created = CartItem.objects.get_or_create(
                user=user,
                product=item.product,
                defaults={'quantity': item.quantity}
            )
            if not created:
                user_item.quantity += item.quantity
                user_item.save()
        guest_items.delete()
        guest.delete()
    except Exception:
        pass

class CartAPIView(APIView):
    def get(self, request):
        guest_token = None
        if request.user.is_authenticated:
            items = CartItem.objects.filter(user=request.user)
        else:
            guest, guest_token = get_or_create_guest(request)
            items = CartItem.objects.filter(guest=guest)

        serializer = CartItemSerializer(items, many=True)
        response_data = {
            "cart_items": serializer.data
        }
        if guest_token:
            response_data["guest_id"] = guest_token

        response = Response(response_data)
        if guest_token:
            response.set_cookie("guest_id", guest_token, max_age=365*24*60*60, httponly=False, samesite="Lax")
        return response

    def post(self, request):
        serializer = CartItemCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        product_id = serializer.validated_data["product_id"]
        quantity = serializer.validated_data["quantity"]
        product = get_object_or_404(Product, id=product_id)

        guest_token = None
        if request.user.is_authenticated:
            cart_item, created = CartItem.objects.get_or_create(
                user=request.user, 
                product=product,
                defaults={"quantity": quantity}
            )
        else:
            guest, guest_token = get_or_create_guest(request)
            cart_item, created = CartItem.objects.get_or_create(
                guest=guest, 
                product=product,
                defaults={"quantity": quantity}
            )

        if not created:
            cart_item.quantity += quantity
            cart_item.save()

        # Fetch updated items
        if request.user.is_authenticated:
            items = CartItem.objects.filter(user=request.user)
        else:
            items = CartItem.objects.filter(guest__guest_token=guest_token)

        read_serializer = CartItemSerializer(items, many=True)
        response_data = {
            "cart_items": read_serializer.data
        }
        if guest_token:
            response_data["guest_id"] = guest_token

        response = Response(response_data, status=status.HTTP_201_CREATED)
        if guest_token:
            response.set_cookie("guest_id", guest_token, max_age=365*24*60*60, httponly=False, samesite="Lax")
        return response

class CartItemDetailAPIView(APIView):
    def patch(self, request, product_id):
        quantity = request.data.get("quantity")
        if quantity is None or int(quantity) <= 0:
            return Response({"detail": "Quantity must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
        
        product = get_object_or_404(Product, id=product_id)
        guest_token = None

        if request.user.is_authenticated:
            cart_item = get_object_or_404(CartItem, user=request.user, product=product)
        else:
            guest_token = request.headers.get("X-Guest-ID") or request.COOKIES.get("guest_id")
            if not guest_token:
                return Response({"detail": "Guest session not found."}, status=status.HTTP_404_NOT_FOUND)
            cart_item = get_object_or_404(CartItem, guest__guest_token=guest_token, product=product)

        cart_item.quantity = int(quantity)
        cart_item.save()

        # Fetch updated items
        if request.user.is_authenticated:
            items = CartItem.objects.filter(user=request.user)
        else:
            items = CartItem.objects.filter(guest__guest_token=guest_token)

        read_serializer = CartItemSerializer(items, many=True)
        return Response({
            "cart_items": read_serializer.data
        })

    def delete(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)
        guest_token = None

        if request.user.is_authenticated:
            CartItem.objects.filter(user=request.user, product=product).delete()
            items = CartItem.objects.filter(user=request.user)
        else:
            guest_token = request.headers.get("X-Guest-ID") or request.COOKIES.get("guest_id")
            if guest_token:
                CartItem.objects.filter(guest__guest_token=guest_token, product=product).delete()
                items = CartItem.objects.filter(guest__guest_token=guest_token)
            else:
                items = []

        read_serializer = CartItemSerializer(items, many=True)
        return Response({
            "message": "Product removed from cart.",
            "cart_items": read_serializer.data
        })

class CartMergeAPIView(APIView):
    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"detail": "User must be authenticated to merge cart."}, status=status.HTTP_401_UNAUTHORIZED)
            
        guest_token = request.data.get("guest_id") or request.headers.get("X-Guest-ID") or request.COOKIES.get("guest_id")
        if not guest_token:
            return Response({"detail": "Guest ID is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        merge_guest_cart(request.user, guest_token)
        
        # Get updated user cart items
        user_items = CartItem.objects.filter(user=request.user)
        return Response({
            "message": "Cart merged successfully.",
            "cart_items": CartItemSerializer(user_items, many=True).data
        })

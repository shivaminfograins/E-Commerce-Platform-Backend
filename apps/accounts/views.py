from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import get_object_or_404
from django.contrib.auth.tokens import (
    PasswordResetTokenGenerator
)
from django.utils.http import (
    urlsafe_base64_encode
)
from django.utils.encoding import (
    force_bytes
)
from django.utils.http import (
    urlsafe_base64_decode
)
from django.conf import settings
from django.core.mail import send_mail


from rest_framework_simplejwt.views import TokenObtainPairView
from .models import Profile, Address, User
from .serializers import (
    ForgotPasswordSerializer,
    RegisterSerializer,
    ProfileSerializer,
    AddressSerializer,
    ResetPasswordSerializer,
    CustomTokenObtainPairSerializer,
)

#-----------------------------------------------------------
# Register API View
# ----------------------------------------------------------
class RegisterAPIView(APIView):
    
    def post(self, request):

        serializer = RegisterSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save()

        return Response(
            {
                "message": "User registered successfully"
            },
            status=status.HTTP_201_CREATED
        )

#----------------------------------------------------------
# Profile API View
# ----------------------------------------------------------   

class ProfileAPIView(APIView):
    
    permission_classes = [IsAuthenticated]

    def get(self, request):

        serializer = ProfileSerializer(
            request.user.profile
        )

        return Response(serializer.data)

    def put(self, request):

        profile = request.user.profile

        serializer = ProfileSerializer(
            profile,
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save()

        return Response(serializer.data)

    def patch(self, request):

        profile = request.user.profile

        serializer = ProfileSerializer(
            profile,
            data=request.data,
            partial=True
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save()

        return Response(serializer.data)
    
#----------------------------------------------------------
# Address List Create API View
# ----------------------------------------------------------
class AddressListCreateAPIView(APIView):
    
    permission_classes = [IsAuthenticated]

    def get(self, request):

        addresses = Address.objects.filter(
            user=request.user
        )

        serializer = AddressSerializer(
            addresses,
            many=True
        )

        return Response(serializer.data)

    def post(self, request):

        serializer = AddressSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save(
            user=request.user
        )

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED
        )

#------------------------------------------------------------------
# Address Detail API View single address get, update and delete
# -----------------------------------------------------------------
class AddressDetailAPIView(APIView):
    
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
    
        return get_object_or_404(
        Address,
        pk=pk,
        user=user
    )

    def get(self, request, pk):

        address = self.get_object(
            pk,
            request.user
        )

        serializer = AddressSerializer(
            address
        )

        return Response(serializer.data)

    def put(self, request, pk):

        address = self.get_object(
            pk,
            request.user
        )

        serializer = AddressSerializer(
            address,
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save()

        return Response(serializer.data)

    def patch(self, request, pk):

        address = self.get_object(
            pk,
            request.user
        )

        serializer = AddressSerializer(
            address,
            data=request.data,
            partial=True
        )

        serializer.is_valid(
            raise_exception=True
        )

        serializer.save()

        return Response(serializer.data)

    def delete(self, request, pk):

        address = self.get_object(
            pk,
            request.user
        )

        address.delete()

        return Response(
            {
                "message": "Address deleted successfully"
            },
            status=status.HTTP_204_NO_CONTENT
        )
#----------------------------------------------------------
# Forgot Password API View
# ----------------------------------------------------------
class ForgotPasswordAPIView(APIView):
    
    def post(self, request):

        serializer = ForgotPasswordSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(
                email=email
            )
        except User.DoesNotExist:
            return Response(
                {"detail": "User with this email does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        uid = urlsafe_base64_encode(
            force_bytes(user.pk)
        )

        token = PasswordResetTokenGenerator().make_token(
            user
        )

        reset_link = (
            f"{settings.FRONTEND_URL.rstrip('/')}/"
            f"reset-password/"
            f"{uid}/{token}/"
        )

        # Send email with reset link
        subject = "Password Reset Request"
        message = (
            f"Hello,\n\n"
            f"You requested a password reset for your E-Commerce account.\n"
            f"Please click the link below to reset your password:\n\n"
            f"{reset_link}\n\n"
            f"If you did not request this, please ignore this email.\n"
        )
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@ecommerce.com"),
            recipient_list=[email],
            fail_silently=False,
        )

        return Response(
            {
                "message":
                "Password reset link generated.",
                "reset_link":
                reset_link
            }
        )
    
#----------------------------------------------------------
# Reset Password API View   
# ----------------------------------------------------------
class ResetPasswordAPIView(APIView):
    
    def post(self, request):

        serializer = ResetPasswordSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        uid = serializer.validated_data["uid"]

        token = serializer.validated_data["token"]

        password = serializer.validated_data[
            "password"
        ]

        try:

            user_id = (
                urlsafe_base64_decode(uid)
                .decode()
            )

            user = User.objects.get(
                pk=user_id
            )

        except (TypeError, ValueError, UnicodeDecodeError, User.DoesNotExist):

            return Response(
                {
                    "detail": "Invalid UID"
                },
                status=400
            )

        if not PasswordResetTokenGenerator(
        ).check_token(
            user,
            token
        ):

            return Response(
                {
                    "detail":
                    "Invalid or expired token"
                },
                status=400
            )

        user.set_password(password)

        user.save()

        return Response(
            {
                "message":
                "Password reset successful"
            }
        )


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    

from rest_framework import serializers
from .models import User, Profile, Address

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "role",
            "is_verified",
        ]
        read_only_fields = [
            "id",
            "role",
            "is_verified",
        ]


class RegisterSerializer(serializers.ModelSerializer):
    
    password = serializers.CharField(
        write_only=True,
        min_length=8
    )

    class Meta:
        model = User
        fields = [
            "email",
            "username",
            "password",
        ]

    def create(self, validated_data):

        return User.objects.create_user(
            email=validated_data["email"],
            username=validated_data["username"],
            password=validated_data["password"],
        )
    
class ProfileSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Profile
        fields = "__all__"
        read_only_fields = ["user"] 


# class AddressSerializer(serializers.ModelSerializer):
    
#     class Meta:
#         model = Address
#         fields = "__all__"
#         read_only_fields = ["user"]

class AddressSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Address

        fields = "__all__"

        read_only_fields = (
            "id",
            "user",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        """
        Ensure only one default address per user.
        """

        request = self.context.get("request")

        if not request:
            return attrs

        if attrs.get("is_default", False):

            Address.objects.filter(
                user=request.user,
                is_default=True,
            ).exclude(
                pk=self.instance.pk if self.instance else None
            ).update(
                is_default=False
            )

        return attrs

#-----------------------------------------------------------
# Password forget Serializers
#_----------------------------------------------------------

class ForgotPasswordSerializer(serializers.Serializer):
    
    email = serializers.EmailField()

    def validate_email(self, value):

        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "User with this email does not exist."
            )

        return value
    
    
#-----------------------------------------------------------
#  Password reset Serializers
# ----------------------------------------------------------
class ResetPasswordSerializer(serializers.Serializer):
    
    uid = serializers.CharField()
    token = serializers.CharField()

    password = serializers.CharField(
        min_length=8,
        write_only=True
    )

    confirm_password = serializers.CharField(
        min_length=8,
        write_only=True
    )

    def validate(self, attrs):

        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {
                    "confirm_password":
                    "Passwords do not match."
                }
            )

        return attrs


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username = serializers.CharField(required=False)
    email = serializers.EmailField(required=False, write_only=True)

    def validate(self, attrs):
        # If frontend sent email instead of username, map it for compatibility
        if "email" in attrs and not attrs.get("username"):
            attrs["username"] = attrs["email"]
            
        data = super().validate(attrs)
        
        # Merge guest cart to user cart upon login
        request = self.context.get("request")
        if request:
            guest_token = request.headers.get("X-Guest-ID") or request.COOKIES.get("guest_id")
            if guest_token:
                from apps.cart.views import _merge_guest_cart
                _merge_guest_cart(self.user, guest_token)

        data["user"] = {
            "id": self.user.id,
            "email": self.user.email,
            "username": self.user.username,
            "role": self.user.role,
        }
        return data
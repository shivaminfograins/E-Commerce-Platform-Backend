from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("customer", "Customer"),
        ("vendor", "Vendor"),
    )

    email = models.EmailField(
        unique=True
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="customer"
    )

    is_verified = models.BooleanField(
        default=False
    )
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.username
    

class Profile(models.Model):
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile"
    )

    phone = models.CharField(
        max_length=15,
        blank=True
    )

    profile_image = models.ImageField(
        upload_to="profiles/",
        blank=True,
        null=True
    )

    date_of_birth = models.DateField(
        blank=True,
        null=True
    )

    def __str__(self):
        return self.user.username
    

# class Address(models.Model):
    
#     user = models.ForeignKey(
#         User,
#         on_delete=models.CASCADE,
#         related_name="addresses"
#     )

#     full_name = models.CharField(
#         max_length=100
#     )

#     phone = models.CharField(
#         max_length=15
#     )

#     address_line_1 = models.CharField(
#         max_length=255
#     )

#     address_line_2 = models.CharField(
#         max_length=255,
#         blank=True
#     )

#     city = models.CharField(
#         max_length=100
#     )

#     state = models.CharField(
#         max_length=100
#     )

#     country = models.CharField(
#         max_length=100
#     )

#     postal_code = models.CharField(
#         max_length=20
#     )

#     is_default = models.BooleanField(
#         default=False
#     )

#     def __str__(self):
#         return f"{self.full_name} - {self.city}"

#------------- New Address Model ---------------
class Address(models.Model):
    
    HOME = "home"
    OFFICE = "office"
    OTHER = "other"

    ADDRESS_TYPE_CHOICES = [
        (HOME, "Home"),
        (OFFICE, "Office"),
        (OTHER, "Other"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="addresses"
    )

    full_name = models.CharField(
        max_length=100
    )

    phone = models.CharField(
        max_length=15
    )

    alternate_phone = models.CharField(
        max_length=15,
        blank=True,
        null=True
    )

    address_line_1 = models.CharField(
        max_length=255
    )

    address_line_2 = models.CharField(
        max_length=255,
        blank=True
    )

    landmark = models.CharField(
        max_length=255,
        blank=True
    )

    city = models.CharField(
        max_length=100
    )

    state = models.CharField(
        max_length=100
    )

    country = models.CharField(
        max_length=100,
        default="India"
    )

    postal_code = models.CharField(
        max_length=20
    )

    address_type = models.CharField(
        max_length=20,
        choices=ADDRESS_TYPE_CHOICES,
        default=HOME
    )

    is_default = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = [
            "-is_default",
            "-created_at"
        ]

    def save(self, *args, **kwargs):

        if self.is_default:
            Address.objects.filter(
                user=self.user,
                is_default=True
            ).exclude(
                pk=self.pk
            ).update(
                is_default=False
            )

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.address_type})"
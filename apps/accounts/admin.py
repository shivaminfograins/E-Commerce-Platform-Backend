from django.contrib import admin
from .models import User, Profile, Address

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "full_name",
        "user",
        "phone",
        "city",
        "state",
        "address_type",
        "is_default",
        "created_at",
    )

    list_filter = (
        "address_type",
        "state",
        "city",
        "is_default",
    )

    search_fields = (
        "full_name",
        "phone",
        "postal_code",
        "city",
        "user__email",
    )

    ordering = (
        "-is_default",
        "-created_at",
    )


from django.contrib.auth.admin import UserAdmin

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Columns in the list view table
    list_display = (
        "username",
        "email",
        "role",
        "is_verified",
        "is_staff",
        "is_active",
        "date_joined",
    )
    list_filter = ("role", "is_verified", "is_staff", "is_active")
    search_fields = ("username", "email")
    ordering = ("-date_joined",)

    # Fields inside the edit form
    fieldsets = UserAdmin.fieldsets + (
        ("Custom Fields", {"fields": ("role", "is_verified")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Custom Fields", {"fields": ("role", "is_verified")}),
    )


admin.site.register(Profile)

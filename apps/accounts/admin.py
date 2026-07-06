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


admin.site.register(User)
admin.site.register(Profile)

from rest_framework.permissions import BasePermission

class IsVendor(BasePermission):
    """
    Allows access only to authenticated users who have a Vendor profile.
    """
    def has_permission(self, request, view):
        return (
            request.user 
            and request.user.is_authenticated 
            and hasattr(request.user, "vendor_profile")
        )


class IsApprovedVendor(BasePermission):
    """
    Allows access only to authenticated users who have an APPROVED Vendor profile.
    """
    def has_permission(self, request, view):
        return (
            request.user 
            and request.user.is_authenticated 
            and hasattr(request.user, "vendor_profile") 
            and request.user.vendor_profile.status == "APPROVED"
        )

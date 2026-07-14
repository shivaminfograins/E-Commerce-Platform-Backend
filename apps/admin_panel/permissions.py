from rest_framework.permissions import BasePermission


class AdminPermission(BasePermission):
    """
    Allows access only to authenticated users who have the role of 'admin'.
    """

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == "admin"
        )

from rest_framework.permissions import BasePermission

from .models import Profile

Role = Profile.Role


def get_role(user):
    """The user's effective role, or None for anonymous users."""
    if not user or not user.is_authenticated:
        return None
    if user.is_staff or user.is_superuser:
        return Role.STAFF
    profile = getattr(user, 'profile', None)  # missing profile -> None
    return profile.role if profile else Role.CUSTOMER


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        return get_role(request.user) in self.roles


class IsStaffRole(_RolePermission):
    roles = (Role.STAFF,)


class IsCustomerOrStaff(_RolePermission):
    roles = (Role.CUSTOMER, Role.STAFF)


class IsStaffOrCourier(_RolePermission):
    roles = (Role.STAFF, Role.COURIER)
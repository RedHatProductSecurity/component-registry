import logging
import uuid
from collections.abc import Collection
from typing import Any

from rest_framework.permissions import BasePermission

from corgi.core.models import RedHatProfile

logger = logging.getLogger(__name__)


# drf's BasePermission seems to use metaclasses in a way mypy doesn't like
class RedHatRolePermission(BasePermission):  # type: ignore[misc]
    """A permission class that only grants access to users with a given role.
    Nb: Users are only required to have ONE of the specified roles, if more than one
    are specified."""

    def has_permission(self, request: Any, view: Any) -> bool:
        if not hasattr(view, "roles_permitted"):
            raise ValueError(f"View {view} doesn't define any permitted roles")

        if not request.user.is_authenticated:
            return False

        # All authenticated users will have a RedHatProfile
        rhat_profile = RedHatProfile.objects.get(user=request.user)

        user_roles = rhat_profile.rhat_roles.strip("[]").split(", ")
        return set(user_roles).intersection(set(view.roles_permitted)) != set()


# drf's BasePermission seems to use metaclasses in a way mypy doesn't like
class RedHatUUIDPermission(BasePermission):  # type: ignore[misc]
    """A permission class that grants access to users specified by rhatUUID."""

    def has_permission(self, request: Any, view: Any) -> bool:
        if not hasattr(view, "uuids_permitted"):
            raise ValueError(f"View {view} doesn't define any permitted UUIDs")

        if not isinstance(view.uuids_permitted, Collection):
            raise TypeError("Permitted UUIDs must be specified in a collection")

        if not all(isinstance(member, uuid.UUID) for member in view.uuids_permitted):
            raise TypeError("Permitted UUIDs list contains a non-UUID member")

        if not request.user.is_authenticated:
            return False

        # All authenticated users will have a RedHatProfile
        user_uuid = RedHatProfile.objects.get(user=request.user).rhat_uuid

        return user_uuid in view.uuids_permitted

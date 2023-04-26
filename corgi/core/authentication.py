import logging
import uuid
from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import User
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from rest_framework.permissions import BasePermission

from corgi.core.models import RedHatProfile

logger = logging.getLogger(__name__)


class CorgiOIDCBackend(OIDCAuthenticationBackend):
    """An extension of mozilla_django_oidc's authentication backend
    which customizes user creation and authentication to support Red
    Hat SSO additional claims."""

    def verify_claims(self, claims: Any) -> bool:
        """Require, at a minimum, that a user have a rhatUUID claim before even trying to
        authenticate them."""
        verified = super(CorgiOIDCBackend, self).verify_claims(claims)
        return verified and "rhatUUID" in claims

    def filter_users_by_claims(self, claims: Any) -> Iterable[User]:
        """The default behavior is to use e-mail, which may not be unique.
        Instead, we use Red Hat UUID, which should be unique and persistent
        between changes to other user claims."""

        # Since verify_claims requires rhatUUID in claims, it will always be here.
        rhat_uuid = claims["rhatUUID"]

        try:
            rhat_profile = RedHatProfile.objects.get(rhat_uuid=rhat_uuid)
            return [rhat_profile.user]

        except RedHatProfile.DoesNotExist:
            logger.info("UUID %s doesn't have a RedHatProfile", rhat_uuid)

        return self.UserModel.objects.none()

    def create_user(self, claims: Any) -> User:
        """Rather than changing the existing Django user model, this stores Red Hat SSO
        claims in a separate model keyed to the created user."""
        user = super(CorgiOIDCBackend, self).create_user(claims)

        # Create a Red Hat Profile for this user
        # Because verify_claims requires rhatUUID, it will always be here.
        _ = RedHatProfile.objects.create(
            rhat_uuid=claims["rhatUUID"],
            rhat_roles=claims.get("groups", ""),
            full_name=claims.get("cn", ""),
            user=user,
        )

        return user

    def update_user(self, user: User, claims: Any) -> User:
        RedHatProfile.objects.filter(user=user).update(
            rhat_uuid=claims["rhatUUID"],
            rhat_roles=claims.get("groups", ""),
            full_name=claims.get("cn", ""),
        )

        return user


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
    """A permission class that grants access to users specified by rhatUUID.
    More than one user can be specified in a non-nested list, set, or tuple
    of strings or UUIDs."""

    ALLOWED_UUID_TYPES = (list, tuple, set, str, uuid.UUID)

    def has_permission(self, request: Any, view: Any) -> bool:
        if not hasattr(view, "uuids_permitted"):
            raise ValueError(f"View {view} doesn't define any permitted UUIDs")

        if type(view.uuids_permitted) not in RedHatUUIDPermission.ALLOWED_UUID_TYPES:
            raise TypeError(
                f"View {view} specifies UUIDs with unsupported type {type(view.uuids_permitted)}"
            )

        if type(view.uuids_permitted) in (list, tuple, set):
            if not all([type(x) in (str, uuid.UUID) for x in view.uuids_permitted]):
                raise TypeError(
                    f"Something other than str or UUID provided for permission in View {view}"
                )

        # Converting str to UUID can raise if the string is not a valid hexadecimal UUID
        if isinstance(view.uuids_permitted, uuid.UUID):
            permitted_uuids = [view.uuids_permitted]
        elif isinstance(view.uuids_permitted, str):
            permitted_uuids = [uuid.UUID(view.uuids_permitted)]
        else:
            str_uuids = [uuid.UUID(s) for s in view.uuids_permitted if isinstance(s, str)]
            nonstr_uuids = [u for u in view.uuids_permitted if not isinstance(u, str)]
            permitted_uuids = str_uuids + nonstr_uuids

        if not request.user.is_authenticated:
            return False

        # All authenticated users will have a RedHatProfile
        user_uuid = RedHatProfile.objects.get(user=request.user).rhat_uuid

        return user_uuid in permitted_uuids

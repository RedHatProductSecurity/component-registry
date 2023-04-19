from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import User
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from corgi.core.models import RedHatProfile


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
        rhat_uuid = claims.get("rhatUUID")

        if not rhat_uuid:
            return self.UserModel.objects.none()

        try:
            rhat_profile = RedHatProfile.objects.get(rhat_uuid=rhat_uuid)
            return [rhat_profile.user]

        except RedHatProfile.DoesNotExist:
            return self.UserModel.objects.none()

        return self.UserModel.objects.none()

    def create_user(self, claims: Any) -> User:
        """Rather than changing the existing Django user model, this stores Red Hat SSO
        claims in a separate model keyed to the created user."""
        if "rhatUUID" not in claims:
            raise AuthenticationFailed("User claims do not include rhatUUID")
        user = super(CorgiOIDCBackend, self).create_user(claims)

        # Create a Red Hat Profile for this user
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

        try:
            rhat_profile = RedHatProfile.objects.get(user=request.user)
        except RedHatProfile.DoesNotExist:
            return False

        user_roles = rhat_profile.rhat_roles.strip("[]").split(", ")
        return set(user_roles).intersection(set(view.roles_permitted)) != set()

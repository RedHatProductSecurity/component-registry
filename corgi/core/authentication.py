from django.core.exceptions import ObjectDoesNotExist
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from rest_framework.permissions import BasePermission

from corgi.core.models import RedHatUser


class CorgiOIDCBackend(OIDCAuthenticationBackend):
    """An extension of mozilla_django_oidc's authentication backend
    which customizes user creation and authentication to support Red
    Hat SSO additional claims."""

    def verify_claims(self, claims):
        """Require, at a minimum, that a user have a rhatUUID claim before even trying to
        authenticate them."""
        verified = super(CorgiOIDCBackend, self).verify_claims(claims)
        return verified and "rhatUUID" in claims

    def filter_user_by_claims(self, claims):
        """The default behavior is to use e-mail, which may not be unique.
        Instead, we use Red Hat UUID, which should be unique and persistent
        between changes to other user claims."""
        rhat_uuid = claims.get("rhatUUID")

        if not rhat_uuid:
            return self.UserModel.objects.none()

        try:
            rhat_user = RedHatUser.objects.get(rhat_uuid=rhat_uuid)
            return [rhat_user.user]

        except ObjectDoesNotExist:
            return self.UserModel.objects.none()

        return self.UserModel.objects.none()

    def create_user(self, claims):
        """Rather than changing the existing Django user model, this stores Red Hat SSO
        claims in a separate model keyed to the created user."""
        assert "rhatUUID" in claims
        user = super(CorgiOIDCBackend, self).create_user(claims)

        # Create a Red Hat User for this user
        _ = RedHatUser.objects.create(
            rhat_uuid=claims["rhatUUID"],
            rhat_roles=claims.get("groups", ""),
            cn=claims.get("cn", ""),
            user=user,
        )

        return user

    def update_user(self, user, claims):
        RedHatUser.objects.filter(user=user).update(
            rhat_uuid=claims["rhatUUID"],
            rhat_roles=claims.get("groups", ""),
            cn=claims.get("cn", ""),
        )

        return user


class RedHatRolePermission(BasePermission):  # type: ignore
    """A permission class that only grants access to users with a given role.
    Nb: Users are only required to have ONE of the specified roles, if more than one
    are specified."""

    def has_permission(self, request, view):
        assert hasattr(view, "roles_permitted")
        try:
            rhat_user = RedHatUser.objects.get(user=request.user)
        except ObjectDoesNotExist:
            return False

        user_roles = rhat_user.rhat_roles.strip("[]").split(", ")
        return set(user_roles).intersection(set(view.roles_permitted)) != set()

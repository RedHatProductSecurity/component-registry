import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token


class Command(BaseCommand):
    help = "Create a user and token for OpenLCS authentication"

    def handle(self, *args, **options) -> str:
        key = os.getenv("OLCS_USER_KEY")
        if key is None:
            raise CommandError("OLCS_USER_KEY environment variable must be set")

        openlcs_user, _ = User.objects.get_or_create(
            username="openlcs", email="prodsec-dev-pelc@redhat.com"
        )
        openlcs_user.set_unusable_password()
        openlcs_user.save()

        if Token.objects.filter(key=key, user=openlcs_user).exists():
            return "Token already set"

        # Because "key" is the token PK, to update the key for a user,
        # existing keys must be deleted first
        Token.objects.filter(user=openlcs_user).delete()
        Token.objects.create(key=key, user=openlcs_user)

        return "Token set"

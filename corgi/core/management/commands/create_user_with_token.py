from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token


class Command(BaseCommand):
    help = "Create a user and specify an authentication token for them"

    def add_arguments(self, parser):
        parser.add_argument(
            "-u",
            "--username",
            required=True,
            type=str,
            help="The username to assign to the created user",
        )
        parser.add_argument(
            "-e", "--email", required=True, type=str, help="The e-mail address of the user"
        )
        parser.add_argument(
            "-t",
            "--token",
            required=True,
            type=str,
            help="The token the user will provide to authenticate",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update token and/or e-mail address if that username already exists",
        )

    def handle(self, *args, **options) -> str:
        # Don't update a username based on a matching e-mail
        if (
            User.objects.filter(email=options["email"])
            .exclude(username=options["username"])
            .exists()
        ):
            raise CommandError("A different user with that e-mail address already exists")

        if User.objects.filter(username=options["username"]).exists():
            user = User.objects.get(username=options["username"])
            created = False
            if user.email != options["email"]:
                if not options["update"]:
                    raise CommandError(
                        "Username exists with different e-mail address and update was not specified"
                    )
                else:
                    self.stdout.write("Updating e-mail address")
                    user = User.objects.get(username=options["username"])
                    user.email = options["email"]
                    user.save()
        else:
            user = User.objects.create_user(username=options["username"], email=options["email"])
            created = True

        # Tokens must be unique, so a new user shouldn't be created with an existing token
        # There's an unfortunate side-effect in that if this is called with --update and a
        # different e-mail is set, but the token already exists, the user's email will be
        # changed but a new token will not be set.
        if Token.objects.filter(key=options["token"]).exists():
            token = Token.objects.get(key=options["token"])
            if token.user == user:
                return "Token already set"
            else:
                if created:
                    user.delete()
                raise CommandError("That token value already exists for a different user")

        if Token.objects.filter(user=user).exists():
            if options["update"]:
                # Because "key" is the token PK, to update the key for a user,
                # existing tokens must be deleted first
                Token.objects.filter(user=user).delete()
            else:
                raise CommandError(
                    f"Token exists for user {options['username']} and update was not specified"
                )

        Token.objects.create(key=options["token"], user=user)

        if created:
            return "User created"
        else:
            return "Token updated"

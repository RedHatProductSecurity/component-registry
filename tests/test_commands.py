from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.authtoken.models import Token

pytestmark = pytest.mark.unit

User = get_user_model()


class CreateUserTokenTest(TestCase):
    def test_create_user(self):
        out = StringIO()
        call_command(
            "create_user_with_token",
            "--username=test_user",
            "--email=test@example.com",
            "--token=123456",
            stdout=out,
        )
        self.assertIn("User created", out.getvalue())
        user = User.objects.get(username="test_user")
        token = Token.objects.get(user=user)
        self.assertEqual(token.key, "123456")
        self.assertEqual(user.username, "test_user")
        self.assertEqual(user.email, "test@example.com")

        # Calling the command with no changes should have no effect
        call_command(
            "create_user_with_token",
            "--username=test_user",
            "--email=test@example.com",
            "--token=123456",
            stdout=out,
        )
        self.assertIn("Token already set", out.getvalue())

    def test_collisions(self):
        ash = User.objects.create_user("ash", "ash@example.com")
        with self.assertRaisesMessage(
            CommandError, "A different user with that e-mail address already exists"
        ):
            call_command(
                "create_user_with_token",
                "--username=aexample",
                "--email=ash@example.com",
                "--token=123456",
            )

        with self.assertRaisesMessage(
            CommandError,
            "Username exists with different e-mail address and update was not specified",
        ):
            call_command(
                "create_user_with_token",
                "--username=ash",
                "--email=aexample@example.com",
                "--token=123456",
            )

        Token.objects.create(user=ash, key="123456")

        with self.assertRaisesMessage(
            CommandError, "That token value already exists for a different user"
        ):
            call_command(
                "create_user_with_token",
                "--username=blair",
                "--email=blair@example.com",
                "--token=123456",
            )

        with self.assertRaisesMessage(
            CommandError, "Token exists for user ash and update was not specified"
        ):
            call_command(
                "create_user_with_token",
                "--username=ash",
                "--email=ash@example.com",
                "--token=654321",
            )

        # Assure existing users don't get deleted when token creation fails
        User.objects.create_user("blair", "blair@example.com")
        with self.assertRaisesMessage(
            CommandError, "That token value already exists for a different user"
        ):
            call_command(
                "create_user_with_token",
                "--username=blair",
                "--email=blair@example.com",
                "--token=123456",
            )

    def test_updates(self):
        ash = User.objects.create_user("ash", "ash@example.com")
        Token.objects.create(user=ash, key="123456")

        call_command(
            "create_user_with_token",
            "--username=ash",
            "--email=aexample@example.com",
            "--token=654321",
            "--update",
        )

        ash.refresh_from_db()
        token = Token.objects.get(user=ash)
        self.assertEqual(ash.email, "aexample@example.com")
        self.assertEqual(token.key, "654321")

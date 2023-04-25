import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings

from corgi.api.views import ControlledAccessTestView, authentication_status
from corgi.core.authentication import CorgiOIDCBackend, RedHatProfile

pytestmark = pytest.mark.unit

User = get_user_model()


class TestAuthentication(TestCase):
    @override_settings(OIDC_AUTH_ENABLED=True)
    @override_settings(OIDC_OP_AUTHORIZATION_ENDPOINT="https://oidc.example.com/auth")
    @override_settings(OIDC_OP_TOKEN_ENDPOINT="https://oidc.example.com/token")
    @override_settings(OIDC_OP_USER_ENDPOINT="https://oidc.example.com/user")
    @override_settings(OIDC_RP_CLIENT_ID="corgi-test")
    @override_settings(OIDC_RP_CLIENT_SECRET="fake_secret")
    def setUp(self):
        self.backend = CorgiOIDCBackend()

    def test_unauthenticated(self):
        """Ensure unauthenticated users get 401"""
        anonymous_req = RequestFactory().get("/api/authentication_status")
        anonymous_req.user = AnonymousUser()
        response = authentication_status(anonymous_req)
        self.assertEqual(response.status_code, 401)

        response = ControlledAccessTestView.as_view()(anonymous_req)
        self.assertEqual(response.status_code, 401)

    @patch("mozilla_django_oidc.auth.requests")
    @patch("mozilla_django_oidc.auth.OIDCAuthenticationBackend.verify_token")
    # @patch("mozilla_django_oidc.contrib.OIDCAuthentication.")
    def test_authenticated(self, token_mock, requests_mock):
        """Test authenticated user with no particular role"""
        user = User.objects.create_user(username="user", email="user@example.com")
        rhp = RedHatProfile.objects.create(
            rhat_uuid=uuid.uuid4(), rhat_roles="", full_name="Example User", user=user
        )
        req = RequestFactory().get(
            "/api/authentication_status",
            {"state": "foo", "code": "bar"},
            HTTP_AUTHORIZATION="Bearer token",
        )
        req.session = {}

        # Fake valid token
        token_mock.return_value = True

        # Fake userinfo responses
        get_json_mock = MagicMock()
        get_json_mock.json.return_value = {
            "username": "user",
            "email": "user@example.com",
            "rhatUUID": str(rhp.rhat_uuid),
        }
        requests_mock.get.return_value = get_json_mock
        post_json_mock = MagicMock()
        post_json_mock.json.return_value = {
            "id_token": "id_token",
            "access_token": "access_granted",
        }
        requests_mock.post.return_value = post_json_mock

        response = authentication_status(req)
        self.assertEqual(response.status_code, 200)

        response = ControlledAccessTestView.as_view()(req)
        # This should maybe be a 401? Needs more research.
        self.assertEqual(response.status_code, 403)

        # Now give the user appropriate roles
        get_json_mock.json.return_value = {
            "username": "user",
            "email": "user@example.com",
            "rhatUUID": str(rhp.rhat_uuid),
            "groups": "[offline_user, prodsec-dev]",
        }
        requests_mock.get.return_value = get_json_mock

        response = ControlledAccessTestView.as_view()(req)
        self.assertEqual(response.status_code, 200)

    @patch("mozilla_django_oidc.auth.requests")
    @patch("mozilla_django_oidc.auth.OIDCAuthenticationBackend.verify_token")
    # @patch("mozilla_django_oidc.contrib.OIDCAuthentication.")
    def test_missing_profile(self, token_mock, requests_mock):
        """If a user authenticates but doesn't exist, both a User and
        RedHatProfile should be created for them. Test the (theoretically
        impossible) case that a Django User exists, but has no corresponding
        RedHatProfile. This *might* happen if e.g. someone's rhatUUID changes.
        In this case, a new profile will be created for the user."""
        _ = User.objects.create_user(
            username="no_profile_user", email="no_profile_user@example.com"
        )

        req = RequestFactory().get(
            "/api/authentication_status",
            {"state": "foo", "code": "bar"},
            HTTP_AUTHORIZATION="Bearer token",
        )
        req.session = {}

        # Fake valid token
        token_mock.return_value = True

        # Fake userinfo responses
        get_json_mock = MagicMock()
        get_json_mock.json.return_value = {
            "username": "no_profile_user",
            "email": "no_profile_user@example.com",
            "rhatUUID": str(uuid.uuid4()),
        }
        requests_mock.get.return_value = get_json_mock
        post_json_mock = MagicMock()
        post_json_mock.json.return_value = {
            "id_token": "id_token",
            "access_token": "access_granted",
        }
        requests_mock.post.return_value = post_json_mock

        response = authentication_status(req)
        self.assertEqual(response.status_code, 200)

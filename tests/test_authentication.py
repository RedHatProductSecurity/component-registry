import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView

from corgi.api.views import (
    ControlledAccessTestView,
    TokenAuthTestView,
    authentication_status,
)
from corgi.core.authentication import (
    CorgiOIDCBackend,
    RedHatProfile,
    RedHatRolePermission,
    RedHatUUIDPermission,
)

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


class TestPermissionRequirements(TestCase):
    """Make sure permission classes require the correct information"""

    def setUp(self):
        self.role_perm = RedHatRolePermission()
        self.uuid_perm = RedHatUUIDPermission()
        self.req = MagicMock()
        self.req.user = AnonymousUser()

    def test_no_permissions_specified(self):
        view = APIView()
        with self.assertRaises(ValueError):
            self.role_perm.has_permission(self.req, view)

    def test_roles_permitted(self):
        role_view = APIView()
        role_view.roles_permitted = ["prodsec-dev"]
        self.assertFalse(self.role_perm.has_permission(self.req, role_view))

    def test_uuids_permitted(self):
        uuid_view = APIView()

        with self.assertRaises(ValueError):
            self.uuid_perm.has_permission(self.req, uuid_view)

        # UUID not in a collection: Not OK
        uuid_view.uuids_permitted = uuid.uuid4()
        with self.assertRaises(TypeError):
            self.uuid_perm.has_permission(self.req, uuid_view)

        # Nested list: Not OK
        uuid_view.uuids_permitted = [
            uuid.uuid4(),
            [uuid.uuid4()],
        ]
        with self.assertRaises(TypeError):
            self.uuid_perm.has_permission(self.req, uuid_view)

        # Single UUID in collection: OK
        uuid_view.uuids_permitted = [uuid.uuid4()]
        self.assertFalse(self.uuid_perm.has_permission(self.req, uuid_view))

        # List of UUIDs: OK
        uuid_view.uuids_permitted = [
            uuid.uuid4(),
            uuid.uuid4(),
        ]
        self.assertFalse(self.uuid_perm.has_permission(self.req, uuid_view))


class TestPermissions(TestCase):
    """Test classes used to authenticate views"""

    def setUp(self):
        self.role_perm = RedHatRolePermission()
        self.role_view = APIView()
        self.role_view.roles_permitted = ["prodsec-dev"]
        self.uuid_perm = RedHatUUIDPermission()
        self.uuid_view = APIView()
        self.uuid_view.uuids_permitted = [uuid.UUID("7384d338-b303-47fd-a4de-545af9889c9f")]
        self.multi_uuid = APIView()
        self.multi_uuid.uuids_permitted = [
            uuid.UUID("b87fd2ac-c42f-44e3-9e88-cd62629b465d"),
            uuid.UUID("e2d2079e-8092-471f-ab89-c086d4b41901"),
            uuid.UUID("7384d338-b303-47fd-a4de-545af9889c9f"),
        ]

    def test_anonymous(self):
        """All permission classes should reject anonymous users"""
        req = MagicMock()
        req.user = AnonymousUser()
        self.assertFalse(self.role_perm.has_permission(req, self.role_view))
        self.assertFalse(self.uuid_perm.has_permission(req, self.uuid_view))

    def test_roles(self):
        # User with no roles
        noroles = User.objects.create_user(username="no_roles", email="no_roles@example.com")

        _ = RedHatProfile.objects.create(
            rhat_uuid=uuid.uuid4(), rhat_roles="", full_name="No Roles User", user=noroles
        )

        req = MagicMock()
        req.user = noroles

        # User with the correct role
        self.assertFalse(self.role_perm.has_permission(req, self.role_view))

        roles = User.objects.create_user(username="roles", email="roles@example.com")

        _ = RedHatProfile.objects.create(
            rhat_uuid=uuid.uuid4(),
            rhat_roles="[prodsec-dev]",
            full_name="No Roles User",
            user=roles,
        )

        req.user = roles

        self.assertTrue(self.role_perm.has_permission(req, self.role_view))

    def test_uuid(self):
        # User with a random UUID
        noaccess = User.objects.create_user(username="noaccess", email="noaccess@example.com")

        _ = RedHatProfile.objects.create(
            rhat_uuid=uuid.uuid4(), rhat_roles="", full_name="No Access User", user=noaccess
        )

        req = MagicMock()
        req.user = noaccess

        self.assertFalse(self.uuid_perm.has_permission(req, self.uuid_view))

        # User with the correct UUID
        access = User.objects.create_user(username="access", email="access@example.com")

        _ = RedHatProfile.objects.create(
            rhat_uuid=uuid.UUID("7384d338-b303-47fd-a4de-545af9889c9f"),
            rhat_roles="",
            full_name="Access User",
            user=access,
        )

        req.user = access

        self.assertTrue(self.uuid_perm.has_permission(req, self.uuid_view))

        # User has one of multiple UUIDs
        self.assertTrue(self.uuid_perm.has_permission(req, self.multi_uuid))


class TestTokenAuth(TestCase):
    """Test local user token-based authentication"""

    def setUp(self):
        self.user = User.objects.create_user(username="test", email="test@example.com")
        self.token = Token.objects.create(user=self.user)

    def test_view(self):
        anonymous_get = RequestFactory().get("/api/token_auth_test")
        response = TokenAuthTestView.as_view()(anonymous_get)
        self.assertEqual(response.status_code, 200)

        anonymous_post = RequestFactory().post("/api/token_auth_test")
        response = TokenAuthTestView.as_view()(anonymous_post)
        self.assertEqual(response.status_code, 401)

        authenticated_get = RequestFactory().get(
            "/api/token_auth_test",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        response = TokenAuthTestView.as_view()(authenticated_get)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"user": "test"})

        authenticated_post = RequestFactory().post(
            "/api/token_auth_test",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        response = TokenAuthTestView.as_view()(authenticated_post)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"user": "test"})

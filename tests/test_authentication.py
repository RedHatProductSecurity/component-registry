import uuid
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView

from corgi.api.views import TokenAuthTestView
from corgi.core.authentication import (
    RedHatProfile,
    RedHatRolePermission,
    RedHatUUIDPermission,
)

pytestmark = pytest.mark.unit

User = get_user_model()


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

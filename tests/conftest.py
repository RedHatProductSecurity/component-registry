import pytest
from rest_framework.test import APIClient

from corgi.api.constants import CORGI_API_VERSION


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """Allow using database automatically, without needing to mark each test"""
    pass


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def api_version():
    return CORGI_API_VERSION


@pytest.fixture
def api_path(api_version):
    return f"/api/{api_version}"


@pytest.fixture
def test_scheme_host():
    return "http://localhost:8008"


def filter_response(response):
    response["headers"].pop("Set-Cookie", None)
    response["headers"].pop("x-ausername", None)
    return response

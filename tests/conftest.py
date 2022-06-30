import pytest
from rest_framework.test import APIClient

from corgi.api.constants import CORGI_API_VERSION


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
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


@pytest.fixture(scope="session")
def vcr_config():
    return {
        "filter_headers": [
            "Authorization",
            "Cookie",
        ],
        "before_record_response": filter_response,
        # Add sensitive query params here if you don't want them appear in the cassette files.
        "filter_query_parameters": [],
        "decode_compressed_response": True,
    }

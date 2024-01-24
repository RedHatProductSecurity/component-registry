import pytest

from corgi.collectors.models import CollectorSpdxLicense
from corgi.collectors.spdx import SPDX_LICENSE_LIST_URL, Spdx
from corgi.tasks.licenses import valid_spdx_license_identifier

pytestmark = pytest.mark.unit


@pytest.mark.django_db
def test_get_license_list(requests_mock):
    with open("tests/data/spdx/licenses.json") as licenses_data:
        requests_mock.get(SPDX_LICENSE_LIST_URL, text=(licenses_data.read()))

    version = Spdx.get_spdx_license_list()
    assert version == "d59b71b"
    assert CollectorSpdxLicense.objects.exists()
    assert CollectorSpdxLicense.objects.get(identifier="0BSD")


@pytest.mark.django_db
def test_valid_spdx_license_identifer(requests_mock):
    with open("tests/data/spdx/licenses.json") as licenses_data:
        requests_mock.get(SPDX_LICENSE_LIST_URL, text=(licenses_data.read()))

    Spdx.get_spdx_license_list()

    assert valid_spdx_license_identifier("0BSD")
    assert valid_spdx_license_identifier("0BSD+")

import os

import pytest

from corgi.collectors.pulp import Pulp

TEST_REPO = "rhel-8-for-aarch64-appstream-rpms__8"
TEST_URL = f"{os.getenv('CORGI_PULP_URL')}/api/v2/repositories/{TEST_REPO}/search/units/"

pytestmark = pytest.mark.unit


def test_get_repo_rpm_data(requests_mock):
    with open(f"tests/data/pulp/{TEST_REPO}-rpms.json") as rpm_data:
        requests_mock.post(TEST_URL, text=rpm_data.read())
    result = Pulp()._get_rpm_data(TEST_REPO)
    assert result == {
        "python3-3.6.8-37.el8": [
            "platform-python-debug-3.6.8-37.el8.aarch64.rpm",
            "platform-python-devel-3.6.8-37.el8.aarch64.rpm",
        ]
    }

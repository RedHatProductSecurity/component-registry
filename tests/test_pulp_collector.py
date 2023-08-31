import os

import pytest

from corgi.collectors.pulp import Pulp

TEST_REPO = "rhel-8-for-aarch64-appstream-rpms__8"
TEST_URL = f"{os.getenv('CORGI_PULP_URL')}/api/v2/repositories/{TEST_REPO}/search/units/"

pytestmark = pytest.mark.unit


def test_fetch_repo_module_data(requests_mock):
    with open(f"tests/data/pulp/{TEST_REPO}-modules.json") as module_data:
        requests_mock.post(TEST_URL, text=module_data.read())
    result = Pulp()._get_module_data(TEST_REPO)
    assert result == {
        "389-ds-1.4-8040020210311214642.866effaa": [
            "389-ds-base-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.src",
            "389-ds-base-debuginfo-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-debugsource-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-devel-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-legacy-tools-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-legacy-tools-debuginfo-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e"
            ".aarch64",
            "389-ds-base-libs-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-libs-debuginfo-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-snmp-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "389-ds-base-snmp-debuginfo-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.aarch64",
            "python3-lib389-0:1.4.3.16-13.module+el8.4.0+10307+74bbfb4e.noarch",
        ],
        "389-ds-1.4-8040020210721074904.96015a92": [
            "389-ds-base-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.src",
            "389-ds-base-debuginfo-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-debugsource-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-devel-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-legacy-tools-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-legacy-tools-debuginfo-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43"
            ".aarch64",
            "389-ds-base-libs-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-libs-debuginfo-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-snmp-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "389-ds-base-snmp-debuginfo-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.aarch64",
            "python3-lib389-0:1.4.3.16-19.module+el8.4.0+11894+f5bb5c43.noarch",
        ],
        "pmdk-1_fileformat_v6-8040020210211161159.9f9e2e7e": [],
    }


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

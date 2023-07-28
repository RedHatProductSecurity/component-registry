import pytest

from corgi.core.fixups import cpe_lookup

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def test_cpe_lookup():
    assert sorted(cpe_lookup("rhel-9.2.0")) == [
        "cpe:/a:redhat:enterprise_linux:9::appstream",
        "cpe:/a:redhat:enterprise_linux:9::crb",
        "cpe:/a:redhat:enterprise_linux:9::highavailability",
        "cpe:/a:redhat:enterprise_linux:9::nfv",
        "cpe:/a:redhat:enterprise_linux:9::realtime",
        "cpe:/a:redhat:enterprise_linux:9::resilientstorage",
        "cpe:/a:redhat:enterprise_linux:9::sap",
        "cpe:/a:redhat:enterprise_linux:9::sap_hana",
        "cpe:/a:redhat:enterprise_linux:9::supplementary",
        "cpe:/o:redhat:enterprise_linux:9::baseos",
        "cpe:/o:redhat:enterprise_linux:9::fastdatapath",
        "cpe:/o:redhat:enterprise_linux:9::hypervisor",
    ]
    assert cpe_lookup("non-existant-product-stream") == set()

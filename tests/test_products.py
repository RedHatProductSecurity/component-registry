import os
from datetime import datetime
from unittest.mock import patch

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.collectors.rhel_compose import RhelCompose
from corgi.core.models import Product, ProductComponentRelation, ProductStream
from corgi.tasks.prod_defs import update_products
from corgi.tasks.rhel_compose import save_compose

pytestmark = pytest.mark.unit

base_url = os.getenv("CORGI_TEST_DOWNLOAD_URL")


def test_fetch_compose_data(requests_mock):
    compose_url = (
        f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}/rhel-8/rel-eng/RHEL-8/latest-RHEL-8.6.0/compose"
    )
    for path in ["composeinfo", "rpms", "osbs", "modules"]:
        with open(f"tests/data/compose/RHEL-8.6.0-20220420.3/{path}.json") as compose:
            requests_mock.get(f"{compose_url}/metadata/{path}.json", text=compose.read())

    compose_data = RhelCompose.fetch_compose_data(compose_url, ["BaseOS", "SAPHANA"])
    expected = (
        "RHEL-8.6.0-20220420.3",
        datetime(2022, 4, 20, 0, 0),
        [
            "curl-7.61.1-22.el8.aarch64.rpm",
            "curl-7.61.1-22.el8.src.rpm",
            "curl-debuginfo-7.61.1-22.el8.aarch64.rpm",
            "curl-debugsource-7.61.1-22.el8.aarch64.rpm",
            "libcurl-7.61.1-22.el8.aarch64.rpm",
            "libcurl-debuginfo-7.61.1-22.el8.aarch64.rpm",
            "libcurl-devel-7.61.1-22.el8.aarch64.rpm",
            "libcurl-minimal-7.61.1-22.el8.aarch64.rpm",
            "libcurl-minimal-debuginfo-7.61.1-22.el8.aarch64.rpm",
            "curl-7.61.1-22.el8.ppc64le.rpm",
            "curl-7.61.1-22.el8.src.rpm",
            "curl-debuginfo-7.61.1-22.el8.ppc64le.rpm",
            "curl-debugsource-7.61.1-22.el8.ppc64le.rpm",
            "libcurl-7.61.1-22.el8.ppc64le.rpm",
            "libcurl-debuginfo-7.61.1-22.el8.ppc64le.rpm",
            "libcurl-devel-7.61.1-22.el8.ppc64le.rpm",
            "libcurl-minimal-7.61.1-22.el8.ppc64le.rpm",
            "libcurl-minimal-debuginfo-7.61.1-22.el8.ppc64le.rpm",
            "curl-7.61.1-22.el8.s390x.rpm",
            "curl-7.61.1-22.el8.src.rpm",
            "curl-debuginfo-7.61.1-22.el8.s390x.rpm",
            "curl-debugsource-7.61.1-22.el8.s390x.rpm",
            "libcurl-7.61.1-22.el8.s390x.rpm",
            "libcurl-debuginfo-7.61.1-22.el8.s390x.rpm",
            "libcurl-devel-7.61.1-22.el8.s390x.rpm",
            "libcurl-minimal-7.61.1-22.el8.s390x.rpm",
            "libcurl-minimal-debuginfo-7.61.1-22.el8.s390x.rpm",
            "curl-7.61.1-22.el8.src.rpm",
            "curl-7.61.1-22.el8.x86_64.rpm",
            "curl-debuginfo-7.61.1-22.el8.i686.rpm",
            "curl-debuginfo-7.61.1-22.el8.x86_64.rpm",
            "curl-debugsource-7.61.1-22.el8.i686.rpm",
            "curl-debugsource-7.61.1-22.el8.x86_64.rpm",
            "libcurl-7.61.1-22.el8.i686.rpm",
            "libcurl-7.61.1-22.el8.x86_64.rpm",
            "libcurl-debuginfo-7.61.1-22.el8.i686.rpm",
            "libcurl-debuginfo-7.61.1-22.el8.x86_64.rpm",
            "libcurl-devel-7.61.1-22.el8.i686.rpm",
            "libcurl-devel-7.61.1-22.el8.x86_64.rpm",
            "libcurl-minimal-7.61.1-22.el8.i686.rpm",
            "libcurl-minimal-7.61.1-22.el8.x86_64.rpm",
            "libcurl-minimal-debuginfo-7.61.1-22.el8.i686.rpm",
            "libcurl-minimal-debuginfo-7.61.1-22.el8.x86_64.rpm",
        ],
        [
            "compat-sap-c++-10-10.2.1-1.el8.ppc64le.rpm",
            "compat-sap-c++-10-10.2.1-1.el8.src.rpm",
            "compat-sap-c++-10-debuginfo-10.2.1-1.el8.ppc64le.rpm",
            "compat-sap-c++-10-debugsource-10.2.1-1.el8.ppc64le.rpm",
            "compat-sap-c++-10-10.2.1-1.el8.src.rpm",
            "compat-sap-c++-10-10.2.1-1.el8.x86_64.rpm",
            "compat-sap-c++-10-debuginfo-10.2.1-1.el8.x86_64.rpm",
            "compat-sap-c++-10-debugsource-10.2.1-1.el8.x86_64.rpm",
        ],
    )
    assert compose_data[0] == expected[0]
    assert compose_data[1] == expected[1]
    srpms = compose_data[2]["srpms"]
    curl_srpm = srpms["curl-7.61.1-22.el8"]
    compat_sap_c = srpms["compat-sap-c++-10-10.2.1-1.el8"]
    assert expected[2] == curl_srpm
    assert expected[3] == compat_sap_c


class MockBrewResult(object):
    pass


@patch("corgi.tasks.rhel_compose._brew_srpm_lookup")
def test_save_compose(mock_brew_srpm_lookup, requests_mock):
    composes = {
        f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}/rhel-8/rel-eng/RHEL-8/RHEL-8.4.0-RC-1.2/compose": [
            "AppStream"
        ],
    }
    compose_url = next(iter(composes))
    for path in ["composeinfo", "rpms", "osbs", "modules"]:
        with open(f"tests/data/compose/RHEL-8.4.0-RC-1.2/{path}.json") as compose:
            requests_mock.get(f"{compose_url}/metadata/{path}.json", text=compose.read())
    result = MockBrewResult()
    result.result = "1533085"
    mock_brew_srpm_lookup.return_value = [
        ("389-ds-base-1.4.3.16-13.module+el8.4.0+10307+74bbfb4e", result)
    ]
    product_stream = ProductStream.objects.create(name="rhel-8.4.0", composes=composes)
    save_compose("rhel-8.4.0")
    relation = ProductComponentRelation.objects.get(product_ref=product_stream)
    assert relation.type == ProductComponentRelation.Type.COMPOSE
    assert relation.external_system_id == "RHEL-8.4.0-20210503.1"


def test_products(requests_mock):
    with open("tests/data/product-definitions.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=prod_defs.read()
        )
    et_product = CollectorErrataProduct.objects.create(
        et_id=152, name="Red Hat ACM", short_name="RHACM"
    )
    et_product_version = CollectorErrataProductVersion.objects.create(
        et_id=1607,
        name="RHEL-8-RHACM-2.4",
        product=et_product,
        brew_tags=["rhacm-2.4-rhel-8-container"],
    )
    et_variant = CollectorErrataProductVariant.objects.create(
        et_id=3657,
        name="8Base-RHACM-2.4",
        product_version=et_product_version,
        cpe="cpe:/a:redhat:acm:2.4::el8",
    )

    update_products()

    assert Product.objects.all().count() == 3

    rhel_product = Product.objects.get(name="rhel")
    assert rhel_product.name == "rhel"
    assert rhel_product.ofuri == "o:redhat:rhel"

    assert "HighAvailability-8.6.0.Z.MAIN.EUS" in rhel_product.product_variants

    rhel_860 = ProductStream.objects.get(name="rhel-8.6.0")
    assert len(rhel_860.composes) == 2

    openshift410z = ProductStream.objects.get(name="openshift-4.10.z")
    assert openshift410z
    assert len(openshift410z.product_variants) == 0
    openshift410z_brew_tags = openshift410z.brew_tags.keys()
    assert len(openshift410z_brew_tags) == 2
    assert "rhaos-4.10-rhel-8-container-released" in openshift410z_brew_tags

    assert len(openshift410z.yum_repositories) == 5

    rhacm24z = ProductStream.objects.get(name="rhacm-2.4.z")
    assert rhacm24z
    assert et_variant.name in rhacm24z.product_variants

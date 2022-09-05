import os
from unittest.mock import patch

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorComposeRhelModule,
    CollectorComposeRPM,
    CollectorComposeSRPM,
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


@patch("corgi.collectors.brew.Brew.brew_srpm_lookup")
@patch("corgi.collectors.brew.Brew.brew_rpm_lookup")
def test_fetch_module_data(mock_brew_rpm_lookup, mock_brew_srpm_lookup, requests_mock):
    compose_url = (
        f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}"
        f"/rhel-8/rel-eng/RHEL-8/latest-RHEL-8.6.0/compose/metadata/"
    )
    with open("tests/data/compose/RHEL-8.6.0-20220420.3/modules.json") as compose:
        requests_mock.get(f"{compose_url}modules.json", text=compose.read())

    rpm_result = MockBrewResult()
    rpm_result.result = {"build_id": 1875692}
    mock_brew_rpm_lookup.return_value = [
        ("389-ds-base-1.4.3.28-6.module+el8.6.0+14129+983ceada.x86_64", rpm_result)
    ]

    srpm_result = MockBrewResult()
    srpm_result.result = 1875729
    mock_brew_srpm_lookup.return_value = [("389-ds-1.4-8060020220204145416.ce3e8c9c", srpm_result)]

    assert CollectorComposeRhelModule.objects.all().count() == 0
    assert CollectorComposeSRPM.objects.all().count() == 0
    assert CollectorComposeRPM.objects.all().count() == 0

    module_srpms = RhelCompose._fetch_module_data(compose_url, ["BaseOS", "SAPHANA"])
    assert 1875729 in list(module_srpms)
    rhel_module = CollectorComposeRhelModule.objects.get(
        nvr="389-ds-1.4-8060020220204145416.ce3e8c9c"
    )
    assert rhel_module.build_id == 1875729
    srpm = CollectorComposeSRPM.objects.get(build_id=1875692)
    assert srpm

    rpm = CollectorComposeRPM.objects.get(
        nvr="389-ds-base-1.4.3.28-6.module+el8.6.0+14129+983ceada.x86_64"
    )
    assert rpm
    assert rpm.rhel_module == rhel_module
    assert rpm.srpm == srpm


@patch("corgi.collectors.brew.Brew.brew_srpm_lookup")
def test_fetch_rpm_data(mock_brew_srpm_lookup, requests_mock):
    compose_url = (
        f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}"
        f"/rhel-8/rel-eng/RHEL-8/RHEL-8.4.0-RC-1.2/compose/metadata"
    )
    with open("tests/data/compose/RHEL-8.4.0-RC-1.2/rpms.json") as module_data:
        requests_mock.get(f"{compose_url}rpms.json", text=module_data.read())
    result = MockBrewResult()
    result.result = "1533085"
    mock_brew_srpm_lookup.return_value = [
        ("389-ds-base-1.4.3.16-13.module+el8.4.0+10307+74bbfb4e", result)
    ]
    srpms = RhelCompose._fetch_rpm_data(compose_url, ["AppStream"])
    assert "1533085" in list(srpms)


class MockBrewResult(object):
    pass


def mock_fetch_rpm_data(compose_url, variants):
    yield "1533085"


@patch("corgi.collectors.rhel_compose.RhelCompose._fetch_rpm_data", new=mock_fetch_rpm_data)
def test_save_compose(requests_mock):
    composes = {
        f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}/rhel-8/rel-eng/RHEL-8/RHEL-8.4.0-RC-1.2/compose": [
            "AppStream"
        ],
    }
    compose_url = next(iter(composes))
    for path in ["composeinfo", "rpms", "osbs", "modules"]:
        with open(f"tests/data/compose/RHEL-8.4.0-RC-1.2/{path}.json") as compose:
            requests_mock.get(f"{compose_url}/metadata/{path}.json", text=compose.read())
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

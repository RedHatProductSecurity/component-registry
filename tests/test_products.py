import os
from unittest.mock import patch

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import Product, ProductComponentRelation, ProductStream
from corgi.tasks.prod_defs import update_products
from corgi.tasks.rhel_compose import load_composes, save_compose

pytestmark = pytest.mark.unit

PRODUCT_DEFINITIONS_CASSETTE = "test_products.yaml"


@pytest.mark.vcr()
@patch("corgi.tasks.rhel_compose.save_compose.delay")
def test_load_composes(mock_delay):
    ProductStream.objects.create(name="rhel-7.6.z")
    ProductStream.objects.create(name="rhel-8.4.0.z")
    load_composes()
    mock_delay.assert_any_call(
        "rhel-7.6.z",
        (
            "RHEL-7.6-20181010.0",
            f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}"  # Comma not missing, joined with below
            "/rhel-7/rel-eng/RHEL-7/RHEL-7.6-20181010.0/compose/metadata/",
        ),
    )
    mock_delay.assert_any_call(
        "rhel-8.4.0.z",
        (
            "RHEL-8.4.0-RC-1.2",
            f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}"  # Comma not missing, joined with below
            "/rhel-8/rel-eng/RHEL-8/RHEL-8.4.0-RC-1.2/compose/metadata/",
        ),
    )


save_compose_test_data = [
    (
        "rhel-8.4.0.z",
        (
            "RHEL-8.4.0-RC-1.2",
            f"{os.getenv('CORGI_TEST_DOWNLOAD_URL')}"  # Comma not missing, joined with below
            "/rhel-8/rel-eng/RHEL-8/RHEL-8.4.0-RC-1.2/compose/metadata/",
        ),
        1,
    ),
]


class MockBrewResult(object):
    pass


@patch("corgi.tasks.rhel_compose._brew_srpm_lookup")
@pytest.mark.parametrize("stream_name, compose_coords, no_of_relations", save_compose_test_data)
def test_save_compose(
    mock_brew_srpm_lookup, stream_name, compose_coords, no_of_relations, requests_mock
):
    for path in ["composeinfo", "rpms", "osbs", "modules"]:
        with open(f"tests/data/compose/{compose_coords[0]}/{path}.json") as compose:
            requests_mock.get(f"{compose_coords[1]}{path}.json", text=compose.read())
    result = MockBrewResult()
    result.result = "1533085"
    mock_brew_srpm_lookup.return_value = [
        ("389-ds-base-1.4.3.16-13.module+el8.4.0+10307+74bbfb4e", result)
    ]
    product_stream = ProductStream.objects.create(name=stream_name)
    save_compose(stream_name, compose_coords)
    relations = ProductComponentRelation.objects.filter(product_ref=product_stream)
    assert len(relations) == no_of_relations
    if len(relations) > 0:
        relation_types = (
            ProductComponentRelation.objects.all().values_list("type", flat=True).distinct()
        )

        assert list(relation_types) == [ProductComponentRelation.Type.COMPOSE]
        relation_sys_ids = (
            ProductComponentRelation.objects.all()
            .values_list("external_system_id", flat=True)
            .distinct()
        )
        assert list(relation_sys_ids) == [compose_coords[0]]


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

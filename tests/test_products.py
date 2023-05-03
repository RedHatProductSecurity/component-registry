import os

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import Product, ProductStream
from corgi.tasks.prod_defs import update_products

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_products(requests_mock):
    with open("tests/data/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        text = text.replace("{CORGI_TEST_DOWNLOAD_URL}", os.getenv("CORGI_TEST_DOWNLOAD_URL"))
        text = text.replace("{CORGI_PULP_URL}", os.getenv("CORGI_PULP_URL"))
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)
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

    assert Product.objects.count() == 4

    rhel_product = Product.objects.get(name="rhel")
    assert rhel_product.name == "rhel"
    assert rhel_product.ofuri == "o:redhat:rhel"

    assert "HighAvailability-8.6.0.Z.MAIN.EUS" in rhel_product.productvariants.values_list(
        "name", flat=True
    )

    rhel_860 = ProductStream.objects.get(name="rhel-8.6.0")
    assert len(rhel_860.composes) == 2

    openshift410z = ProductStream.objects.get(name="openshift-4.10.z")
    assert openshift410z.productvariants.count() == 0
    # Stream "cpes" is a dynamically-generated property, different than the "cpe" field
    # Which should include the stream's CPE + all the child variant CPEs (if any)
    assert openshift410z.cpes == ()
    assert len(openshift410z.brew_tags) == 2
    assert "rhaos-4.10-rhel-8-container-released" in openshift410z.brew_tags

    assert len(openshift410z.yum_repositories) == 5

    rhacm24z = ProductStream.objects.get(name="rhacm-2.4.z")
    assert et_variant.name in rhacm24z.productvariants.values_list("name", flat=True)
    # Stream "cpes" is a dynamically-generated property, different than the "cpe" field
    # Which should include the stream's CPE + all the child variant CPEs (if any)
    assert et_variant.cpe in rhacm24z.cpes


@pytest.mark.django_db
def test_skip_brew_tag_linking_for_buggy_products(requests_mock):
    """RHEL-7-SATELLITE-6.10 has a brew_tag for 6.7 version, which means the 7Server-Satellite67
    ProductVariant gets incorrectly associated with the rhn_satellite_6.7 product stream"""

    with open("tests/data/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        text = text.replace("{CORGI_TEST_DOWNLOAD_URL}", os.getenv("CORGI_TEST_DOWNLOAD_URL"))
        text = text.replace("{CORGI_PULP_URL}", os.getenv("CORGI_PULP_URL"))
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    et_product = CollectorErrataProduct.objects.create(
        et_id=103, name="Red Hat Satellite 6", short_name="SATELLITE"
    )
    CollectorErrataProductVersion.objects.create(
        et_id=1571,
        name="RHEL-7-SATELLITE-6.10",
        product=et_product,
        brew_tags=["satellite-6.7.0-rhel-7"],
    )

    update_products()

    rhn_satellite_67 = ProductStream.objects.get(name="rhn_satellite_6.7")
    assert "satellite-6.7.0-rhel-7" in rhn_satellite_67.brew_tags
    assert rhn_satellite_67.productvariants.count() == 0

    rhn_satellite_610 = ProductStream.objects.get(name="rhn_satellite_6.10")
    sat_610_variants = rhn_satellite_610.productvariants.get_queryset()
    assert len(sat_610_variants) == 2
    for variant in sat_610_variants:
        assert variant.name in ("7Server-Capsule610", "7Server-Satellite610")

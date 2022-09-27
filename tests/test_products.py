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


def test_products(requests_mock):
    with open("tests/data/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        text = text.replace("{CORGI_TEST_DOWNLOAD_URL}", os.getenv("CORGI_TEST_DOWNLOAD_URL"))
        text = text.replace("{CORGI_TEST_PULP_URL}", os.getenv("CORGI_TEST_PULP_URL"))
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

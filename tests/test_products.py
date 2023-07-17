import os

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import Product, ProductStream, ProductVersion
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
    # Test that a Collector model with missing CPE defaults to empty "" string
    # Using NULL / None as the default causes issues during ingestion
    et_variant_without_cpe = CollectorErrataProductVariant.objects.create(
        et_id=3658,
        name="8Base2-RHACM-2.4",
        product_version=et_product_version,
    )
    assert et_variant_without_cpe.cpe == ""
    assert et_product_version.variants.all().count() == 2

    update_products()

    assert Product.objects.count() == 5

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
def test_cpe_parsing(requests_mock):
    with open("tests/data/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    openshift_4 = ProductVersion.objects.get(name="openshift-4")
    assert openshift_4.cpe_patterns == ["cpe:/a:redhat:openshift:4"]


@pytest.mark.django_db
def test_match_cpe_patterns(requests_mock):
    et_variant_cpes = [
        "cpe:/a:redhat:openshift_gitops:1::el8",
        "cpe:/a:redhat:openshift_gitops:1.1::el8",
        "cpe:/a:redhat:openshift_gitops:1.10::el8",
    ]

    product = CollectorErrataProduct.objects.create(name="product", et_id=1)
    product_version = CollectorErrataProductVersion.objects.create(
        name="product_version", et_id=10, product=product
    )
    et_id = 100
    for cpe in et_variant_cpes:
        CollectorErrataProductVariant.objects.create(
            name=str(et_id), et_id=et_id, product_version=product_version, cpe=cpe
        )
        et_id += 1

    with open("tests/data/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    product_version = ProductVersion.objects.get(name="gitops-1")
    assert sorted(product_version.cpes_matching_patterns) == sorted(et_variant_cpes)


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


# We stip -candidate from tags before persisting Collector models
brew_tag_streams = [
    # In the actual ET data the brew tag is gitops-1.7-rhel-8-candidate
    # This matches ps_update_stream gitops-1.7, which has a brew tag
    # gitops-1.7-rhel-8-candidate
    ("8Base-GitOps-1.7", "gitops-1.7-rhel-8", "gitops-1.7"),
    # In the actual ET data the brew tag is rhaos-4.10-rhel-8-candidate
    # This matches ps_update_stream openshift-4.10.z, which has a brew tag
    # rhaos-4.10-rhel-8-container-released
    ("OSE-4.10-RHEL-8", "rhaos-4.10-rhel-8", "openshift-4.10.z"),
    # In the actual ET data the brew tag is rhacm-2.4-rhel-7-container-candidate
    # This matches the ps_update_stream rhacm-2.4.z, which has a brew tag
    # rhacm-2.4-rhel-7-container-released
    ("RHACM-2.4", "rhacm-2.4-rhel-7-container", "rhacm-2.4.z"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("variant_name,brew_tag,stream_name", brew_tag_streams)
def test_brew_tag_matching(variant_name, brew_tag, stream_name, requests_mock):
    et_product = CollectorErrataProduct.objects.create(et_id=1, name="product")
    et_product_version = CollectorErrataProductVersion.objects.create(
        et_id=10, name="product_version", product=et_product, brew_tags=[brew_tag]
    )
    CollectorErrataProductVariant.objects.create(
        et_id=100, name=variant_name, product_version=et_product_version
    )

    with open("tests/data/product-definitions.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=(prod_defs.read())
        )

    update_products()

    stream = ProductStream.objects.get(name=stream_name)
    assert stream.productvariants.first().name == variant_name

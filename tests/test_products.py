import os
from unittest.mock import patch

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import (
    Product,
    ProductComponentRelation,
    ProductStream,
    ProductVariant,
    ProductVersion,
)
from corgi.tasks.prod_defs import (
    _find_by_cpe,
    _match_and_save_stream_cpes,
    update_products,
)
from tests.factories import (
    ProductStreamFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
)

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_products(requests_mock):
    with open("tests/data/prod_defs/product-definitions.json") as prod_defs:
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
    assert et_variant.cpe not in rhacm24z.cpes
    assert et_variant.cpe in rhacm24z.cpes_from_brew_tags


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
def test_stream_brew_tags_removed(mock_remove, requests_mock):
    with open("tests/data/prod_defs/proddefs-update.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()
    assert not mock_remove.called

    stream = ProductStream.objects.get(name="stream")
    assert stream.brew_tags == {"test_tag": False}

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="test_tag",
        product_ref="stream",
        software_build=sb,
        type=ProductComponentRelation.Type.BREW_TAG,
    )

    with open("tests/data/prod_defs/proddefs-update-tag-removed.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    assert not ProductComponentRelation.objects.filter(software_build=sb).exists()
    assert mock_remove.called_with(
        (
            str(sb.pk),
            "ProductStream",
            stream.pk,
        )
    )


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
def test_stream_new_brew_tags_with_old_builds(mock_remove, requests_mock):
    with open("tests/data/prod_defs/proddefs-update-tag-with-old-build.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    stream = ProductStream.objects.get(name="stream")
    assert "test_tag" in stream.brew_tags
    assert "another_tag" in stream.brew_tags
    assert not mock_remove.called

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="test_tag",
        product_ref="stream",
        software_build=sb,
        type=ProductComponentRelation.Type.BREW_TAG,
    )

    ProductComponentRelation.objects.create(
        external_system_id="another_tag",
        product_ref="stream",
        software_build=sb,
        type=ProductComponentRelation.Type.BREW_TAG,
    )

    with open("tests/data/prod_defs/proddefs-update-tag-with-old-build-removed.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    stream.refresh_from_db()
    assert "test_tag" not in stream.brew_tags
    assert "another_tag" in stream.brew_tags
    assert not ProductComponentRelation.objects.filter(external_system_id="test_tag").exists()
    assert not mock_remove.called


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
def test_stream_variants_with_old_builds(mock_remove, requests_mock):
    with open("tests/data/prod_defs/proddefs-update-variant-with-old-build.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    et_product = CollectorErrataProduct.objects.create(
        et_id=1, name="product", short_name="product"
    )
    et_product_version = CollectorErrataProductVersion.objects.create(
        et_id=10,
        name="version",
        product=et_product,
    )
    CollectorErrataProductVariant.objects.create(
        et_id=100,
        name="variant",
        product_version=et_product_version,
    )

    update_products()

    stream = ProductStream.objects.get(name="stream")
    assert "test_tag" in stream.brew_tags
    assert not mock_remove.called

    variant = ProductVariant.objects.get(name="variant")
    assert variant in stream.productvariants.get_queryset()

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="test_tag",
        product_ref="stream",
        software_build=sb,
        type=ProductComponentRelation.Type.BREW_TAG,
    )

    ProductComponentRelation.objects.create(
        external_system_id="12345",
        product_ref="variant",
        software_build=sb,
        type=ProductComponentRelation.Type.ERRATA,
    )

    with open(
        "tests/data/prod_defs/proddefs-update-variant-with-old-build-removed.json"
    ) as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    stream.refresh_from_db()
    assert "test_tag" not in stream.brew_tags
    assert variant in stream.productvariants.get_queryset()
    assert not ProductComponentRelation.objects.filter(external_system_id="test_tag").exists()
    assert not mock_remove.called


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
def test_stream_yum_repos_removed(mock_remove, requests_mock):
    with open("tests/data/prod_defs/proddefs-update.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()
    assert not mock_remove.called

    stream = ProductStream.objects.get(name="yum_stream")
    assert stream.yum_repositories

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="https://example.com/rhel-8/compose/yum_stream/",
        product_ref="yum_stream",
        software_build=sb,
        type=ProductComponentRelation.Type.YUM_REPO,
    )

    with open("tests/data/prod_defs/proddefs-update-repo-removed.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    stream.refresh_from_db()
    assert not stream.yum_repositories
    assert not ProductComponentRelation.objects.filter(software_build=sb).exists()
    assert mock_remove.called_with(
        (
            str(sb.pk),
            "ProductStream",
            stream.pk,
        )
    )


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
def test_stream_yum_repos_to_errata_info_not_removed(mock_remove, requests_mock):
    with open("tests/data/prod_defs/proddefs-update.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()
    assert not mock_remove.called

    stream = ProductStream.objects.get(name="yum_stream")
    assert stream.yum_repositories

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="https://example.com/rhel-8/compose/yum_stream/",
        product_ref="yum_stream",
        software_build=sb,
        build_id=sb.build_id,
        type=ProductComponentRelation.Type.YUM_REPO,
    )

    ProductComponentRelation.objects.create(
        external_system_id="10000",
        product_ref="8Base-CertSys-10.6",
        software_build=sb,
        build_id=sb.build_id,
        type=ProductComponentRelation.Type.ERRATA,
    )

    with open("tests/data/prod_defs/proddefs-update-repo-to-errata-info.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    stream.refresh_from_db()

    assert "8Base-CertSys-10.6" in stream.productvariants.values_list("name", flat=True)
    assert stream.et_product_versions
    assert not ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.YUM_REPO
    ).exists()
    assert not mock_remove.called


@pytest.mark.django_db
def test_cpe_parsing(requests_mock):
    with open("tests/data/prod_defs/product-definitions.json") as prod_defs:
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

    with open("tests/data/prod_defs/product-definitions.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    product_version = ProductVersion.objects.get(name="gitops-1")
    assert sorted(product_version.cpes_matching_patterns) == sorted(et_variant_cpes)


@pytest.mark.django_db
def test_match_stream_version_cpe_patterns_matching_prefix():
    product_version = ProductVersionFactory(
        cpes_matching_patterns=[
            "cpe:/a:redhat:openshift_ironic:4.12::el9",
            "cpe:/a:redhat:openshift:4.1::el8",
            "cpe:/a:redhat:openshift:4.1::el7",
            "cpe:/a:redhat:openshift:4.12::el9",
            "cpe:/a:redhat:openshift:4.12::el8",
        ]
    )

    ProductStreamFactory(productversions=product_version, version="4.1", name="stream_41")
    ProductStreamFactory(productversions=product_version, version="4.12", name="stream_412")

    _match_and_save_stream_cpes(product_version)

    stream41 = ProductStream.objects.get(name="stream_41")
    stream412 = ProductStream.objects.get(name="stream_412")

    assert stream41.cpes_matching_patterns == [
        "cpe:/a:redhat:openshift:4.1::el8",
        "cpe:/a:redhat:openshift:4.1::el7",
    ]
    assert stream412.cpes_matching_patterns == [
        "cpe:/a:redhat:openshift_ironic:4.12::el9",
        "cpe:/a:redhat:openshift:4.12::el9",
        "cpe:/a:redhat:openshift:4.12::el8",
    ]


@pytest.mark.django_db
def test_match_stream_version_cpe_patterns_no_el_suffix():
    product_version = ProductVersionFactory(
        cpes_matching_patterns=[
            "cpe:/a:redhat:network_satellite_managed_db:5.8::el6",
            "cpe:/a:redhat:network_satellite:5.8::el6",
            "cpe:/a:redhat:network_proxy:5.8::el6",
            "cpe:/a:redhat:rhel_rhn_tools:5",
        ]
    )

    ProductStreamFactory(productversions=product_version, version="5", name="stream_5")
    ProductStreamFactory(productversions=product_version, version="5.8", name="stream_58")

    _match_and_save_stream_cpes(product_version)

    stream5 = ProductStream.objects.get(name="stream_5")
    stream58 = ProductStream.objects.get(name="stream_58")

    assert stream5.cpes_matching_patterns == ["cpe:/a:redhat:rhel_rhn_tools:5"]
    assert stream58.cpes_matching_patterns == [
        "cpe:/a:redhat:network_satellite_managed_db:5.8::el6",
        "cpe:/a:redhat:network_satellite:5.8::el6",
        "cpe:/a:redhat:network_proxy:5.8::el6",
    ]


@pytest.mark.django_db
def test_find_by_cpe():
    product = CollectorErrataProduct.objects.create(name="product", et_id=1)
    product_version = CollectorErrataProductVersion.objects.create(
        name="product_version", et_id=10, product=product
    )
    cpe = "cpe:/a:redhat:quay:3::el8"
    CollectorErrataProductVariant.objects.create(
        name="product_variant", et_id=100, product_version=product_version, cpe=cpe
    )

    results = _find_by_cpe(["cpe:/a:redhat:quay:3"])

    assert cpe in results


@pytest.mark.django_db
def test_find_by_cpe_substring():
    product = CollectorErrataProduct.objects.create(name="product", et_id=1)
    product_version = CollectorErrataProductVersion.objects.create(
        name="product_version", et_id=10, product=product
    )
    cpe = "cpe:/a:redhat:openshift_pipelines:1.7::el8"
    CollectorErrataProductVariant.objects.create(
        name="product_variant", et_id=100, product_version=product_version, cpe=cpe
    )

    results = _find_by_cpe(["cpe:/a:redhat:openshift_pipelines:1"])

    assert cpe in results


# We stip -candidate from tags before persisting Collector models
brew_tag_streams = [
    # In the actual ET data the brew tag is gitops-1.7-rhel-8-candidate
    # This matches ps_update_stream gitops-1.7, which has a brew tag
    # gitops-1.7-rhel-8-candidate
    (
        "8Base-GitOps-1.7",
        "cpe:/a:redhat:openshift_gitops:1.7::el8",
        "gitops-1.7-rhel-8",
        "gitops-1.7",
    ),
    # In the actual ET data the brew tag is rhaos-4.10-rhel-8-candidate
    # This matches ps_update_stream openshift-4.10.z, which has a brew tag
    # rhaos-4.10-rhel-8-container-released
    (
        "8Base-RHOSE-4.10",
        "cpe:/a:redhat:openshift:4.10::el8",
        "rhaos-4.10-rhel-8",
        "openshift-4.10.z",
    ),
    # In the actual ET data the brew tag is rhacm-2.4-rhel-7-container-candidate
    # This matches the ps_update_stream rhacm-2.4.z, which has a brew tag
    # rhacm-2.4-rhel-7-container-released
    ("RHACM-2.4", "cpe:/a:redhat:acm:2.4::el7", "rhacm-2.4-rhel-7-container", "rhacm-2.4.z"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("variant_name,cpe,brew_tag,stream_name", brew_tag_streams)
def test_brew_tag_matching(variant_name, cpe, brew_tag, stream_name, requests_mock):
    et_product = CollectorErrataProduct.objects.create(et_id=1, name="product")
    et_product_version = CollectorErrataProductVersion.objects.create(
        et_id=10, name="product_version", product=et_product, brew_tags=[brew_tag]
    )
    CollectorErrataProductVariant.objects.create(
        et_id=100, name=variant_name, product_version=et_product_version, cpe=cpe
    )

    with open("tests/data/prod_defs/product-definitions.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=(prod_defs.read())
        )

    update_products()

    stream = ProductStream.objects.get(name=stream_name)
    assert stream.productvariants.get_queryset().count() == 0
    assert stream.cpes_from_brew_tags == [cpe]


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_remove_product_from_build.delay")
@patch("corgi.tasks.prod_defs.slow_save_taxonomy.delay")
def test_multi_variant_streams(mock_save, mock_remove, requests_mock):
    """Test that errata_info variants are only attached to an active stream where that stream
    shares errata_info with an inactive stream"""
    with open("tests/data/prod_defs/proddefs-update-multi-stream-variant.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()
    assert not mock_remove.called

    ga_stream = ProductStream.objects.get(name="ga_stream")
    assert ga_stream.productvariants.get_queryset().count() == 1
    z_stream = ProductStream.objects.get(name="z_stream")
    assert z_stream.productvariants.get_queryset().count() == 0
    assert z_stream.yum_repositories

    sb = SoftwareBuildFactory()

    ProductComponentRelation.objects.create(
        external_system_id="https://example.com/rhel-8/compose/yum_stream/",
        product_ref="z_stream",
        software_build=sb,
        type=ProductComponentRelation.Type.YUM_REPO,
    )

    ProductComponentRelation.objects.create(
        external_system_id="some_repo",
        product_ref="8Base-CertSys-10.4",
        software_build=sb,
        type=ProductComponentRelation.Type.CDN_REPO,
    )

    with open("tests/data/prod_defs/proddefs-update-multi-stream-variant-update.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    ga_stream.refresh_from_db()
    assert ga_stream.productvariants.get_queryset().count() == 0
    z_stream.refresh_from_db()
    assert z_stream.productvariants.get_queryset().count() == 1
    assert not ProductComponentRelation.objects.filter(product_ref="z_stream").exists()
    assert mock_remove.called_once_with(
        (
            str(sb.pk),
            "ProductStream",
            z_stream.pk,
        )
    )
    assert mock_save.called_once_with(sb.build_id, sb.build_type)


@pytest.mark.django_db
def test_multi_variant_active_streams(requests_mock):
    """Tests that multiple runs of update_products with multiple active streams sharing errata_info
    does not cause the variants to move back and forth between the streams"""
    with open("tests/data/prod_defs/proddefs-update-multi-stream-variant-active.json") as prod_defs:
        text = prod_defs.read()
        requests_mock.get(f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=text)

    update_products()

    ga_stream = ProductStream.objects.get(name="ga_stream")
    assert ga_stream.productvariants.get_queryset().count() == 0
    z_stream = ProductStream.objects.get(name="z_stream")
    assert z_stream.productvariants.get_queryset().count() == 1

    # Call update_products again with the same proddefs-update-mult-stream-variant-active.json
    update_products()

    ga_stream = ProductStream.objects.get(name="ga_stream")
    assert ga_stream.productvariants.get_queryset().count() == 0
    z_stream = ProductStream.objects.get(name="z_stream")
    assert z_stream.productvariants.get_queryset().count() == 1

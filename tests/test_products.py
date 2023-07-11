import os
from unittest.mock import call, patch

import pytest
from django.conf import settings

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import (
    ComponentNode,
    Product,
    ProductComponentRelation,
    ProductStream,
    ProductVariant,
)
from corgi.tasks.prod_defs import slow_update_builds_for_variant, update_products
from tests.conftest import setup_product
from tests.factories import (
    ComponentFactory,
    ProductStreamFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
)

pytestmark = pytest.mark.unit


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
@patch("corgi.tasks.prod_defs.slow_update_builds_for_variant.apply_async")
def test_products(mock_update_builds, requests_mock):
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

    # Assert mock_update_builds was not called because no variants moved between streams
    assert not mock_update_builds.called


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_update_builds_for_variant.apply_async")
def test_skip_brew_tag_linking_for_buggy_products(mock_update_builds, requests_mock):
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

    assert not mock_update_builds.called


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_update_builds_for_variant.apply_async")
def test_stream_variants_updated(mock_update_builds, requests_mock):
    # Creates ProductStream, "stream", with an existing ProductVariant called "1"
    # The ps_update_stream loaded from proddefs-*.json below has a matching name "stream"
    stream, variant = setup_product("stream")
    # Sets up a CollectorErrataProductVersion with a matching brew tag
    et_product = CollectorErrataProduct.objects.create(et_id=1, name="et_product_1")
    et_version = CollectorErrataProductVersion.objects.create(
        et_id=10, product=et_product, name="et_stream_1", brew_tags=["test_tag"]
    )
    # Attaching a different Variant, called "2" to the CollectorErrataProductVersion
    CollectorErrataProductVariant.objects.create(et_id=100, name="2", product_version=et_version)

    with open("tests/data/proddefs-update-variants.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=(prod_defs.read())
        )

    update_products()

    # Verify that a new ProductVariant called "2" was created
    variant_2 = ProductVariant.objects.get(name="2")
    assert stream == variant_2.productstreams

    # Verify that the original variant is still associated with the stream
    assert stream == variant.productstreams

    assert not mock_update_builds.called


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_update_builds_for_variant.apply_async")
def test_stream_variants_moved_to_new_stream(mock_update_builds, requests_mock):
    # Creates ProductStream, "stream", with an existing ProductVariant called "1"
    # The ps_update_stream loaded from proddefs-*.json below has a matching name "stream"
    stream, variant = setup_product("stream")

    # Verify that the original variant is associated with the stream
    assert stream == variant.productstreams

    # Sets up a CollectorErrataProductVersion with a matching brew tag
    et_product = CollectorErrataProduct.objects.create(et_id=1, name="et_product_1")
    et_version = CollectorErrataProductVersion.objects.create(
        et_id=10, product=et_product, name="et_stream_1", brew_tags=["two_streams_with_same_tag"]
    )
    # This is the same variant as the original to the CollectorErrataProductVersion
    CollectorErrataProductVariant.objects.create(et_id=100, name="1", product_version=et_version)

    with open("tests/data/proddefs-update-variants-moved.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=(prod_defs.read())
        )

    update_products()

    variant = ProductVariant.objects.get(name="1")
    new_stream = ProductStream.objects.get(name="new_stream")
    # The variant is now associated with the new stream
    assert variant.productstreams == new_stream
    assert mock_update_builds.called_with("1", ("new_stream", "stream"), countdown=300)


def _create_builds_and_components(stream):
    stream_build = SoftwareBuildFactory()
    stream_component = ComponentFactory(software_build=stream_build)
    stream_component_node = ComponentNode.objects.create(
        parent=None, obj=stream_component, type=ComponentNode.ComponentNodeType.SOURCE
    )
    stream_child_component = ComponentFactory()
    ComponentNode.objects.create(
        parent=stream_component_node,
        obj=stream_child_component,
        type=ComponentNode.ComponentNodeType.PROVIDES,
    )
    stream_component.productstreams.add(stream)
    stream_component.productversions.add(stream.productversions)
    stream_component.products.add(stream.products)
    return stream_build, stream_component, stream_child_component


@pytest.mark.django_db
def test_slow_update_builds_for_variant_same_version():
    stream, _ = setup_product("stream")
    new_stream = ProductStreamFactory(
        name="new_stream", products=stream.products, productversions=stream.productversions
    )

    # Create builds and components for stream
    stream_build, stream_component, stream_child_component = _create_builds_and_components(stream)

    # Relate the stream_build to the variant "1" created by setup_product
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA,
        product_ref="1",
        software_build=stream_build,
        build_id=stream_build.build_id,
        build_type=stream_build.build_type,
    )

    slow_update_builds_for_variant(
        "1",
        (
            new_stream.name,
            stream.name,
        ),
    )

    assert stream_component.productstreams.filter(name="new_stream").exists()
    assert not stream_component.productstreams.filter(name="stream").exists()

    assert stream_child_component.productstreams.filter(name="new_stream").exists()
    assert not stream_child_component.productstreams.filter(name="stream").exists()


@pytest.mark.django_db
def test_slow_update_builds_for_variant_same_product():
    stream, _ = setup_product("stream")
    new_version = ProductVersionFactory(products=stream.products)
    new_stream = ProductStreamFactory(
        name="new_stream", products=stream.products, productversions=new_version
    )
    # Create builds and components for stream
    stream_build, stream_component, stream_child_component = _create_builds_and_components(stream)

    # Relate the stream_build to the variant "1" created by setup_product
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA,
        product_ref="1",
        software_build=stream_build,
        build_id=stream_build.build_id,
        build_type=stream_build.build_type,
    )

    slow_update_builds_for_variant(
        "1",
        (
            new_stream.name,
            stream.name,
        ),
        (
            new_version.name,
            stream.productversions.name,
        ),
    )

    assert stream_component.productstreams.filter(name="new_stream").exists()
    assert not stream_component.productstreams.filter(name="stream").exists()

    assert stream_child_component.productstreams.filter(name="new_stream").exists()
    assert not stream_child_component.productstreams.filter(name="stream").exists()

    assert stream_component.productversions.filter(name=new_version.name).exists()
    assert not stream_component.productversions.filter(name=stream.productversions.name).exists()


@pytest.mark.django_db
def test_slow_update_builds_for_variant_different_product():
    stream, _ = setup_product("stream")
    new_product = Product.objects.create(name="new_product")
    new_version = ProductVersionFactory(products=new_product)
    new_stream = ProductStreamFactory(name="new_stream", productversions=new_version)
    # Create builds and components for stream
    stream_build, stream_component, stream_child_component = _create_builds_and_components(stream)

    # Relate the stream_build to the variant "1" created by setup_product
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA,
        product_ref="1",
        software_build=stream_build,
        build_id=stream_build.build_id,
        build_type=stream_build.build_type,
    )

    slow_update_builds_for_variant(
        "1",
        (
            new_stream.name,
            stream.name,
        ),
        (
            new_version.name,
            stream.productversions.name,
        ),
        (
            new_product.name,
            stream.products.name,
        ),
    )

    assert stream_component.productstreams.filter(name="new_stream").exists()
    assert not stream_component.productstreams.filter(name="stream").exists()

    assert stream_child_component.productstreams.filter(name="new_stream").exists()
    assert not stream_child_component.productstreams.filter(name="stream").exists()

    assert stream_component.productversions.filter(name=new_version.name).exists()
    assert not stream_component.productversions.filter(name=stream.productversions.name).exists()

    assert stream_component.products.filter(name=new_product.name).exists()
    assert not stream_component.products.filter(name=stream.products.name).exists()


@pytest.mark.django_db
@patch("corgi.tasks.prod_defs.slow_update_builds_for_variant.apply_async")
def test_product_variant_index_out_of_range(mock_update_builds, requests_mock):
    # This test was written because when loading the actual product_definitions the product
    # taxonomy was being truncated by stream which shared a brew_tag. The issue was fixed
    # by not called variant.save_product_taxonomy in the `for et_variant in et_pv.variants.all()`
    # loop in `parse_variants_from_brew_tags function`
    existing_stream, existing_variant = setup_product()

    et_product = CollectorErrataProduct.objects.create(et_id=1, name="et_product_1")
    # Sets up 2 CollectorErrataProductVersions with matching brew tags
    # The brew tag matches the 2 new streams in product_definitions
    et_version_2 = CollectorErrataProductVersion.objects.create(
        et_id=12, product=et_product, name="et_stream_2", brew_tags=["two_streams_with_same_tag"]
    )
    et_version = CollectorErrataProductVersion.objects.create(
        et_id=11, product=et_product, name="et_stream_1", brew_tags=["two_streams_with_same_tag"]
    )
    # Variant 2 is a new Variant, while "1" was created by setup_product
    CollectorErrataProductVariant.objects.create(et_id=102, name="2", product_version=et_version_2)
    CollectorErrataProductVariant.objects.create(et_id=101, name="1", product_version=et_version)

    assert existing_stream.name != "stream"
    assert existing_stream.name != "new_stream"
    assert existing_stream.products != "product"

    with open("tests/data/proddefs-update-variants-moved.json") as prod_defs:
        requests_mock.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions", text=(prod_defs.read())
        )

    update_products()

    assert mock_update_builds.call_args_list == [
        call(
            args=(
                "1",
                ("stream", existing_stream.name),
                ("version", existing_stream.productversions.name),
                ("product", existing_stream.products.name),
            ),
            kwargs={"countdown": 300},
        ),
        # Variant 1 and 2 are then moved from "stream" to the new_stream
        call(args=("1", ("new_stream", "stream"), None, None), kwargs={"countdown": 300}),
        call(args=("2", ("new_stream", "stream"), None, None), kwargs={"countdown": 300}),
    ]

    # Assert that "new_stream" was created after "stream"
    new_stream = ProductStream.objects.get(name="new_stream")
    stream = ProductStream.objects.get(name="stream")
    assert new_stream.created_at > stream.created_at

    # Assert both new variants are only associated with the new_stream (created 2nd)
    variant = ProductVariant.objects.get(name="1")
    assert variant.productstreams == new_stream

    variant_2 = ProductVariant.objects.get(name="2")
    assert variant_2.productstreams == new_stream

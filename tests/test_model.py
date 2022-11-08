import pytest
from django.apps import apps
from django.db.utils import IntegrityError, ProgrammingError
from packageurl import PackageURL

from corgi.core.constants import CONTAINER_DIGEST_FORMATS
from corgi.core.models import (
    Component,
    ComponentNode,
    Product,
    ProductComponentRelation,
    ProductNode,
    ProductStream,
    ProductVersion,
    SoftwareBuild,
)

from .factories import (
    ComponentFactory,
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

pytestmark = pytest.mark.unit


def test_product_model():
    p1 = ProductFactory(name="RHEL")
    assert Product.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL"


def test_productversion_model():
    p1 = ProductVersionFactory(name="RHEL")
    assert ProductVersion.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL:8"


def test_productstream_model():
    p1 = ProductStreamFactory(name="RHEL")
    assert ProductStream.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL:8.2.z"
    assert p1.cpe == "cpe:/o:redhat:enterprise_linux:9"


def test_cpes():
    p1 = ProductFactory(name="RHEL")
    pv1a = ProductVersionFactory(name="RHEL-7", version="7")
    pv1b = ProductVersionFactory(name="RHEL-8", version="8")
    ps1 = ProductStreamFactory(name="Ansercanagicus")
    ps2 = ProductStreamFactory(name="Ansercaerulescens")
    ps3 = ProductStreamFactory(name="Brantabernicla", cpe="cpe:/o:redhat:Brantabernicla:8")

    node1 = ProductNode.objects.create(parent=None, obj=p1, object_id=p1.pk)
    assert node1
    node2a = ProductNode.objects.create(parent=node1, obj=pv1a, object_id=pv1a.pk)
    assert node2a
    node2b = ProductNode.objects.create(parent=node1, obj=pv1b, object_id=pv1b.pk)
    assert node2b
    node3a = ProductNode.objects.create(parent=node2a, obj=ps1, object_id=ps1.pk)
    assert node3a
    node3b = ProductNode.objects.create(parent=node2b, obj=ps2, object_id=ps2.pk)
    assert node3b
    node3c = ProductNode.objects.create(parent=node2b, obj=ps3, object_id=ps3.pk)
    assert node3c

    assert pv1a.cpes == ("cpe:/o:redhat:enterprise_linux:9",)
    assert sorted(p1.cpes) == [
        "cpe:/o:redhat:Brantabernicla:8",
        "cpe:/o:redhat:enterprise_linux:9",
    ]


def test_nevra():
    no_release = ComponentFactory(release="", version="1")
    assert not no_release.nvr.endswith("-")
    package_url = PackageURL.from_string(no_release.purl)
    # container image components get their purl version from the digest value, not version & release
    if no_release.type == Component.Type.CONTAINER_IMAGE:
        assert not package_url.qualifiers["tag"].endswith("-")
    else:
        assert not package_url.version.endswith("-")
    # epoch is a property of Component which retrieves the value for meta_attr
    no_epoch = ComponentFactory()
    assert ":" not in no_epoch.nevra
    no_arch = ComponentFactory(arch="")
    assert not no_arch.nevra.endswith("-")


def test_product_taxonomic_queries():
    rhel, rhel_7, _, rhel_8, _, rhel_8_2, _ = create_product_hierarchy()

    assert rhel.product_streams == ["rhel-7.1", "rhel-8.1", "rhel-8.2"]
    assert rhel_8.product_streams == ["rhel-8.1", "rhel-8.2"]
    assert rhel_7.products == ["RHEL"]
    assert rhel_8_2.products == ["RHEL"]


def create_product_hierarchy():
    rhel = ProductFactory(name="RHEL")
    rhel_7 = ProductVersionFactory(name="rhel-7", version="7")
    rhel_8 = ProductVersionFactory(name="rhel-8", version="8")
    rhel_7_1 = ProductStreamFactory(name="rhel-7.1")
    rhel_8_1 = ProductStreamFactory(name="rhel-8.1")
    rhel_8_2 = ProductStreamFactory(name="rhel-8.2", cpe="cpe:/o:redhat:8.2")
    rhel_8_2_base = ProductVariantFactory(name="Base8-test")
    rhel_node = ProductNode.objects.create(parent=None, obj=rhel, object_id=rhel.pk)
    rhel_7_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_7, object_id=rhel_7.pk)
    rhel_8_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_8, object_id=rhel_8.pk)
    ProductNode.objects.create(parent=rhel_7_node, obj=rhel_7_1, object_id=rhel_7_1.pk)
    ProductNode.objects.create(parent=rhel_8_node, obj=rhel_8_1, object_id=rhel_8_1.pk)
    rhel_8_2_node = ProductNode.objects.create(
        parent=rhel_8_node, obj=rhel_8_2, object_id=rhel_8_2.pk
    )
    ProductNode.objects.create(parent=rhel_8_2_node, obj=rhel_8_2_base, object_id=rhel_8_2_base.pk)

    for product_model in (rhel, rhel_7, rhel_7_1, rhel_8, rhel_8_1, rhel_8_2, rhel_8_2_base):
        product_model.save_product_taxonomy()
    return rhel, rhel_7, rhel_7_1, rhel_8, rhel_8_1, rhel_8_2, rhel_8_2_base


def test_component_model():
    c1 = SrpmComponentFactory(name="curl")
    assert Component.objects.get(name="curl") == c1


def test_container_purl():
    container = ContainerImageComponentFactory()
    # When a container doesn't get a digest meta_attr
    assert "@" not in container.purl
    example_digest = "sha256:blah"
    container.meta_attr = {"digests": {CONTAINER_DIGEST_FORMATS[0]: example_digest}}
    container.save()
    assert example_digest in container.purl
    assert "arch" not in container.purl
    assert container.name in container.purl
    example_digest = "sha256:blah"
    repo_name = "node-exporter-rhel8"
    repository_url = f"registry.redhat.io/rhacm2/{repo_name}"
    container.meta_attr = {
        "digests": {CONTAINER_DIGEST_FORMATS[0]: example_digest},
        "repository_url": repository_url,
        "name_from_label": repo_name,
    }
    container.arch = "x86_64"
    container.save()
    assert example_digest in container.purl
    assert "x86_64" in container.purl
    assert repo_name in container.purl.split("?")[0]
    assert repository_url in container.purl


def test_component_provides():
    upstream = ComponentFactory(namespace=Component.Namespace.UPSTREAM)
    upstream_node, _ = upstream.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=upstream.purl
    )
    dev_comp = ComponentFactory(name="dev", type=Component.Type.NPM)
    dev_comp.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV, parent=upstream_node, purl=dev_comp.purl
    )
    assert dev_comp.purl in upstream.get_provides_purls()


def test_software_build_model():
    sb1 = SoftwareBuildFactory(
        type=SoftwareBuild.Type.BREW,
        meta_attr={"build_id": 9999, "a": 1, "b": 2, "brew_tags": ["RHSA-123-123"]},
    )
    assert SoftwareBuild.objects.get(build_id=sb1.build_id) == sb1
    c1 = ComponentFactory(type=Component.Type.RPM, name="curl", software_build=sb1)
    assert Component.objects.get(name="curl") == c1


def test_get_roots():
    srpm = SrpmComponentFactory()
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl
    )
    rpm = ComponentFactory(type=Component.Type.RPM)
    rpm_cnode, _ = rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=srpm_cnode, purl=rpm.purl
    )
    assert rpm.get_roots == [srpm_cnode]
    assert srpm.get_roots == [srpm_cnode]

    nested = ComponentFactory()
    nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=rpm_cnode, purl=nested.purl
    )
    assert nested.get_roots == [srpm_cnode]

    container = ContainerImageComponentFactory()
    container_cnode, _ = container.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=container.purl
    )
    container_rpm = ComponentFactory(type=Component.Type.RPM)
    container_rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
    )
    assert not container_rpm.get_roots
    assert container.get_roots == [container_cnode]

    container_source = ComponentFactory(
        namespace=Component.Namespace.UPSTREAM, type=Component.Type.GITHUB
    )
    container_source_cnode, _ = container_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
    )
    assert container_source.get_roots == [container_cnode]
    container_nested = ComponentFactory(type=Component.Type.NPM)
    container_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_nested.purl,
    )
    assert container_nested.get_roots == [container_cnode]


def test_product_component_relations():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    _, _, rhel_7_1, _, _, _, _ = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.COMPOSE, product_ref=rhel_7_1.name, build_id=build_id
    )
    srpm = SrpmComponentFactory(software_build=sb)
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_7_1.ofuri in c.product_streams


def test_product_component_relations_errata():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    _, _, _, _, _, rhel_8_2, rhel_8_2_base = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA, product_ref=rhel_8_2_base.name, build_id=build_id
    )
    srpm = SrpmComponentFactory(software_build=sb)
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_8_2.ofuri in c.product_streams


def test_product_stream_builds():
    rhel_8_2_build = SoftwareBuildFactory()
    rhel_8_2_base_build = SoftwareBuildFactory()
    rhel_7_1_build = SoftwareBuildFactory()
    rhel, rhel_7, rhel_7_1, _, rhel_8_1, rhel_8_2, rhel_8_2_base = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.COMPOSE,
        # This is a product stream ref
        product_ref=rhel_8_2.name,
        build_id=rhel_8_2_build.build_id,
    )
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.CDN_REPO,
        # This is a product variant ref, and also a child of rhel_8_2 stream
        product_ref=rhel_8_2_base.name,
        build_id=rhel_8_2_base_build.build_id,
    )
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.BREW_TAG,
        # This is a product stream ref only
        product_ref=rhel_7_1.name,
        build_id=rhel_7_1_build.build_id,
    )
    # Test we can find product variant builds
    rhel_8_2_builds = [int(b) for b in rhel_8_2.builds]
    assert rhel_8_2_build.build_id in rhel_8_2_builds
    # Test we can find both product variant and product stream builds when they are ancestors
    assert rhel_8_2_base_build.build_id in rhel_8_2_builds
    # Test we can find product stream builds
    assert rhel_7_1_build.build_id in [int(b) for b in rhel_7_1.builds]
    # Test we can find builds from product stream children of product version
    assert rhel_7_1_build.build_id in [int(b) for b in rhel_7.builds]
    # Test products have all builds
    rhel_builds = [int(b) for b in rhel.builds]
    assert rhel_8_2_build.build_id in rhel_builds
    assert rhel_7_1_build.build_id in rhel_builds
    assert rhel_8_2_base_build.build_id in rhel_builds
    # Test that builds from another stream don't get included
    assert rhel_8_2_build.build_id not in [int(b) for b in rhel_8_1.builds]


def test_component_errata():
    sb = SoftwareBuildFactory()
    c = ComponentFactory(software_build=sb)
    ProductComponentRelationFactory(
        build_id=sb.build_id, external_system_id="RHSA-1", type=ProductComponentRelation.Type.ERRATA
    )
    assert "RHSA-1" in c.errata


def test_get_upstream():
    srpm = SrpmComponentFactory()
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl
    )
    rpm = ComponentFactory(type=Component.Type.RPM)
    rpm_cnode, _ = rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=srpm_cnode, purl=rpm.purl
    )
    srpm_upstream = ComponentFactory(namespace=Component.Namespace.UPSTREAM)
    srpm_upstream_cnode, _ = srpm_upstream.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=srpm_cnode, purl=srpm_upstream.purl
    )
    assert rpm.get_upstreams() == [srpm_upstream.purl]


def test_get_upstream_container():
    container = ContainerImageComponentFactory()
    container_cnode, _ = container.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=container.purl
    )
    container_rpm = ComponentFactory(type=Component.Type.RPM)
    container_rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
    )
    assert container_rpm.get_upstreams() == []

    container_source = ContainerImageComponentFactory()
    container_source_cnode, _ = container_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
    )

    container_nested = ComponentFactory(type=Component.Type.NPM)
    container_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_source.purl,
    )
    assert container_nested.get_upstreams() == [container_source.purl]

    container_o_source = ComponentFactory(
        name="contain_upstream_other",
        type=Component.Type.PYPI,
        namespace=Component.Namespace.UPSTREAM,
    )
    container_o_source_cnode, _ = container_o_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_o_source.purl,
    )

    container_other_nested = ComponentFactory(type=Component.Type.NPM)
    container_other_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_o_source_cnode,
        purl=container_other_nested.purl,
    )
    assert container_other_nested.get_upstreams() == [container_o_source.purl]


def test_duplicate_insert_fails():
    """Test that DB constraints block inserting nodes with same (type, parent, purl)"""
    component = ComponentFactory()
    root = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=root, purl="child", obj=component
    )
    with pytest.raises(IntegrityError):
        # Inserting the same node a second time should fail with an IntegrityError
        ComponentNode.objects.create(
            type=ComponentNode.ComponentNodeType.SOURCE, parent=root, purl="child", obj=component
        )


def test_duplicate_insert_fails_for_null_parent():
    """Test that DB constraints block inserting nodes with same (type, parent=None, purl)"""
    component = ComponentFactory()
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
    )
    with pytest.raises(IntegrityError):
        # Inserting the same node a second time should fail with an IntegrityError
        ComponentNode.objects.create(
            type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl="root", obj=component
        )


def test_querysets_are_unordered_by_default():
    """For all models, assert that the model Meta doesn't define an ordering
    Also assert that the base .objects queryset does not contain SQL to sort the objects
    """
    for model in apps.get_app_config("core").get_models():
        assert model._meta.ordering == []
        assert "Sort " not in model.objects.get_queryset().explain()


def test_mptt_properties_are_unordered_by_default():
    """Assert that MPTT model properties (pnodes, cnodes) don't define an ordering"""

    c = ComponentFactory()
    cnode = ComponentNode(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=c.purl, obj=c
    )
    cnode.save()
    assert "Sort " not in c.cnodes.explain()
    assert cnode._meta.ordering == []
    assert c._meta.ordering == []

    p = ProductFactory()
    pnode = ProductNode(parent=None, obj=p)
    pnode.save()
    assert "Sort " not in p.pnodes.explain()
    assert pnode._meta.ordering == []
    assert p._meta.ordering == []


def test_queryset_ordering_succeeds():
    """Test that .distinct() without field name or order_by() works correctly
    Also test that .distinct() with field name or order_by() works correctly
    """
    # Ordered by version: (a, a), (c, c), (first_b, b), (last_b, b)
    # Ordered by description: (a, a), (first_b, b), (last_b, b), (c, c)
    ProductFactory(name="a", version="a", description="a")
    ProductFactory(name="b", version="first_b", description="b")
    ProductFactory(name="c", version="last_b", description="b")
    ProductFactory(name="d", version="c", description="c")

    # .values_list("description", flat=True).distinct() will succeed, with any of:
    # nothing, .order_by(), or .order_by("description") in front of .values_list

    # no order_by at all inherits any ordering fields the model Meta defines (None)
    unordered_products = Product.objects.get_queryset()
    assert len(unordered_products.values_list("description", flat=True).distinct()) == 3

    # .order_by() removes any ordering field from the result queryset
    ordered_products = unordered_products.order_by()
    assert len(ordered_products.values_list("description", flat=True).distinct()) == 3

    # .order_by("description") adds "description" ordering field to the result queryset
    ordered_products = unordered_products.order_by("description")
    assert len(ordered_products.values_list("description", flat=True).distinct()) == 3


def test_queryset_ordering_fails():
    """Test that .distinct() with non-matching ordering fails the way we expect
    Also test that .distinct("field") with non-matching ordering fails the way we expect
    """
    ProductFactory(version="a", description="a")
    ProductFactory(version="first_b", description="b")
    ProductFactory(version="last_b", description="b")
    ProductFactory(version="c", description="c")

    # .order_by("version").values_list("description", flat=True).distinct() will fail
    # because order_by("version") adds a hidden field to the result queryset we're SELECTing
    # .distinct() looks at (order_by field, values_list field) together when checking duplicates
    unordered_products = Product.objects.get_queryset()

    # .distinct() with no field name looks at both "description" field from values_list
    # and "version" field from order_by, even though only "description" is in the final result
    misordered_products = unordered_products.order_by("version")
    assert len(misordered_products.values_list("description", flat=True).distinct()) == 4

    # SELECT DISTINCT ON expressions must match initial ORDER BY expressions
    with pytest.raises(ProgrammingError):
        len(misordered_products.distinct("description"))

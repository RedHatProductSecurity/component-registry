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

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


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
    assert p1.ofuri == "o:redhat:RHEL:8.2.0.z"
    assert p1.cpe == "cpe:/o:redhat:enterprise_linux:8"


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
def test_cpes():
    p1 = ProductFactory(name="RHEL")
    pv1 = ProductVersionFactory(name="RHEL-7", version="7", products=p1)
    pv2 = ProductVersionFactory(name="RHEL-8", version="8", products=p1)
    ps1 = ProductStreamFactory(name="Ansercanagicus", products=p1, productversions=pv1)
    ps2 = ProductStreamFactory(name="Ansercaerulescens", products=p1, productversions=pv2)
    ps3 = ProductStreamFactory(
        name="Brantabernicla",
        cpe="cpe:/o:redhat:Brantabernicla:8",
        products=p1,
        productversions=pv2,
    )

    p1node = ProductNode.objects.create(parent=None, obj=p1)
    assert p1node
    pv1node = ProductNode.objects.create(parent=p1node, obj=pv1)
    assert pv1node
    pv2node = ProductNode.objects.create(parent=p1node, obj=pv2)
    assert pv2node
    ps1node = ProductNode.objects.create(parent=pv1node, obj=ps1)
    assert ps1node
    ps2node = ProductNode.objects.create(parent=pv2node, obj=ps2)
    assert ps2node
    ps3node = ProductNode.objects.create(parent=pv2node, obj=ps3)
    assert ps3node

    assert pv1.cpes == ("cpe:/o:redhat:enterprise_linux:8",)
    assert sorted(p1.cpes) == [
        "cpe:/o:redhat:Brantabernicla:8",
        "cpe:/o:redhat:enterprise_linux:8",
    ]


def test_nevra():
    for component_type in Component.Type.values:
        no_release = ComponentFactory(
            name=component_type, release="", version="1", type=component_type
        )
        assert not no_release.nvr.endswith("-")
        package_url = PackageURL.from_string(no_release.purl)
        # container images get their purl version from the digest value, not version & release
        if component_type == Component.Type.CONTAINER_IMAGE:
            assert not package_url.qualifiers["tag"].endswith("-")
        else:
            assert not package_url.version.endswith("-")
        # epoch is a property of Component which retrieves the value for meta_attr
        no_epoch = ComponentFactory(name=component_type, type=component_type)
        assert ":" not in no_epoch.nevra
        no_arch = ComponentFactory(name=component_type, arch="", type=component_type)
        assert not no_arch.nevra.endswith("-")


def test_product_taxonomic_queries():
    rhel, rhel_7, _, rhel_8, _, rhel_8_2, _ = create_product_hierarchy()

    assert sorted(rhel.productstreams.values_list("name", flat=True)) == [
        "rhel-7.1",
        "rhel-8.1",
        "rhel-8.2",
    ]
    assert sorted(rhel_8.productstreams.values_list("name", flat=True)) == ["rhel-8.1", "rhel-8.2"]
    assert rhel_7.products.name == rhel_8_2.products.name == "RHEL"


def create_product_hierarchy():
    # TODO: Factory should probably create nodes for model
    #  Move these somewhere for reuse - common.py helper method, or fixtures??
    rhel = ProductFactory(name="RHEL")
    rhel_node = ProductNode.objects.create(parent=None, obj=rhel)

    rhel_7 = ProductVersionFactory(name="rhel-7", version="7", products=rhel)
    rhel_7_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_7)

    rhel_7_1 = ProductStreamFactory(name="rhel-7.1", products=rhel, productversions=rhel_7)
    ProductNode.objects.create(parent=rhel_7_node, obj=rhel_7_1)

    rhel_8 = ProductVersionFactory(name="rhel-8", version="8", products=rhel)
    rhel_8_node = ProductNode.objects.create(parent=rhel_node, obj=rhel_8)

    rhel_8_1 = ProductStreamFactory(name="rhel-8.1", products=rhel, productversions=rhel_8)
    ProductNode.objects.create(parent=rhel_8_node, obj=rhel_8_1)

    rhel_8_2 = ProductStreamFactory(
        name="rhel-8.2", cpe="cpe:/o:redhat:8.2", products=rhel, productversions=rhel_8
    )
    rhel_8_2_node = ProductNode.objects.create(parent=rhel_8_node, obj=rhel_8_2)

    rhel_8_2_base = ProductVariantFactory(
        name="Base8-test", products=rhel, productversions=rhel_8, productstreams=rhel_8_2
    )
    ProductNode.objects.create(parent=rhel_8_2_node, obj=rhel_8_2_base)

    return rhel, rhel_7, rhel_7_1, rhel_8, rhel_8_1, rhel_8_2, rhel_8_2_base


def test_component_model():
    c1 = SrpmComponentFactory(name="curl")
    assert Component.objects.get(name="curl") == c1


def test_container_purl():
    # TODO: Failed due to "assert arch not in purl"
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
    upstream = ComponentFactory(name="upstream", namespace=Component.Namespace.UPSTREAM)
    upstream_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=upstream.purl,
        defaults={"obj": upstream},
    )
    dev_comp = ComponentFactory(name="dev", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV,
        parent=upstream_node,
        purl=dev_comp.purl,
        defaults={"obj": dev_comp},
    )
    assert dev_comp.purl in upstream.get_provides_purls(using="default")


def test_software_build_model():
    sb1 = SoftwareBuildFactory(
        type=SoftwareBuild.Type.BREW,
        meta_attr={"build_id": 9999, "a": 1, "b": 2, "brew_tags": ["RHSA-123-123"]},
    )
    assert SoftwareBuild.objects.get(build_id=sb1.build_id) == sb1
    c1 = ComponentFactory(type=Component.Type.RPM, name="curl", software_build=sb1)
    assert Component.objects.get(name="curl") == c1


def test_get_roots():
    srpm = SrpmComponentFactory(name="srpm")
    srpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=srpm.purl,
        defaults={"obj": srpm},
    )
    rpm = ComponentFactory(name="rpm", type=Component.Type.RPM)
    rpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_cnode,
        purl=rpm.purl,
        defaults={"obj": rpm},
    )
    assert rpm.get_roots == [srpm_cnode]
    assert srpm.get_roots == [srpm_cnode]

    nested = ComponentFactory(name="nested")
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=rpm_cnode,
        purl=nested.purl,
        defaults={"obj": nested},
    )
    assert nested.get_roots == [srpm_cnode]

    container = ContainerImageComponentFactory(name="container")
    container_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=container.purl,
        defaults={"obj": container},
    )
    container_rpm = ComponentFactory(name="container_rpm", type=Component.Type.RPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
        defaults={"obj": container_rpm},
    )
    assert not container_rpm.get_roots
    assert container.get_roots == [container_cnode]

    container_source = ComponentFactory(
        name="container_source", namespace=Component.Namespace.UPSTREAM, type=Component.Type.GITHUB
    )
    container_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
        defaults={"obj": container_source},
    )
    assert container_source.get_roots == [container_cnode]
    container_nested = ComponentFactory(name="container_nested", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_nested.purl,
        defaults={"obj": container_nested},
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
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl, obj=srpm
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_7_1 in c.productstreams.get_queryset()


def test_product_component_relations_errata():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    _, _, _, _, _, rhel_8_2, rhel_8_2_base = create_product_hierarchy()
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA, product_ref=rhel_8_2_base.name, build_id=build_id
    )
    srpm = SrpmComponentFactory(software_build=sb)
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=srpm.purl, obj=srpm
    )
    sb.save_product_taxonomy()
    c = Component.objects.get(uuid=srpm.uuid)
    assert rhel_8_2 in c.productstreams.get_queryset()


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
    srpm = SrpmComponentFactory(name="srpm")
    srpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=srpm.purl,
        defaults={"obj": srpm},
    )
    rpm = ComponentFactory(name="rpm", type=Component.Type.RPM)
    rpm_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_cnode,
        purl=rpm.purl,
        defaults={"obj": rpm},
    )
    srpm_upstream = ComponentFactory(name="srpm_upstream", namespace=Component.Namespace.UPSTREAM)
    srpm_upstream_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=srpm_cnode,
        purl=srpm_upstream.purl,
        defaults={"obj": srpm_upstream},
    )
    assert sorted(rpm.get_upstreams_purls()) == [srpm_upstream.purl]


def test_get_upstream_container():
    container = ContainerImageComponentFactory(name="container")
    container_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=container.purl,
        defaults={"obj": container},
    )
    container_rpm = ComponentFactory(name="container_rpm", type=Component.Type.RPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_cnode,
        purl=container_rpm.purl,
        defaults={"obj": container_rpm},
    )
    assert container_rpm.get_upstreams_purls() == set()

    container_source = ContainerImageComponentFactory(name="container_source")
    container_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
        defaults={"obj": container_source},
    )

    container_nested = ComponentFactory(name="container_nested", type=Component.Type.NPM)
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_source_cnode,
        purl=container_source.purl,
        defaults={"obj": container_nested},
    )
    assert sorted(container_nested.get_upstreams_purls()) == [container_source.purl]

    container_o_source = ComponentFactory(
        name="contain_upstream_other",
        type=Component.Type.PYPI,
        namespace=Component.Namespace.UPSTREAM,
    )
    container_o_source_cnode, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_o_source.purl,
        defaults={"obj": container_o_source},
    )

    container_other_nested = ComponentFactory(
        name="container_nested_other", type=Component.Type.NPM
    )
    ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=container_o_source_cnode,
        purl=container_other_nested.purl,
        defaults={"obj": container_other_nested},
    )
    assert sorted(container_other_nested.get_upstreams_purls()) == [container_o_source.purl]


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
    ProductFactory(name="a", version="a", description="a")
    ProductFactory(name="b", version="first_b", description="b")
    ProductFactory(name="c", version="last_b", description="b")
    ProductFactory(name="d", version="c", description="c")

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

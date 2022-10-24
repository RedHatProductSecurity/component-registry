import pytest
from django.db.utils import IntegrityError

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

    assert pv1a.cpes == ["cpe:/o:redhat:enterprise_linux:9"]
    assert sorted(p1.cpes) == [
        "cpe:/o:redhat:Brantabernicla:8",
        "cpe:/o:redhat:enterprise_linux:9",
    ]


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

    container_source = ComponentFactory(namespace=Component.Namespace.UPSTREAM)
    container_source_cnode, _ = container_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=container_cnode,
        purl=container_source.purl,
    )
    assert container_source.get_roots == [container_cnode]
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

    container_source = ComponentFactory(
        name="container_upstream",
        type=Component.Type.CONTAINER_IMAGE,
        namespace=Component.Namespace.UPSTREAM,
    )
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

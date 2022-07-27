import pytest

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
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
)

pytestmark = pytest.mark.unit


def test_product_model():
    p1 = ProductFactory(name="RHEL")
    assert Product.objects.get(name="RHEL") == p1
    assert p1.ofuri == "o:redhat:RHEL"


def test_product_related_errata():
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.ERRATA, product_ref="Base"
    )
    p = ProductFactory()
    relations = p.get_product_component_relations(["Base"], only_errata=True)
    assert relations.exists()
    relations = p.get_product_component_relations(["rhel"], only_errata=True)
    assert not relations.exists()


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

    node1 = ProductNode.objects.create(parent=None, obj=p1)
    assert node1
    node2a = ProductNode.objects.create(parent=node1, obj=pv1a)
    assert node2a
    node2b = ProductNode.objects.create(parent=node1, obj=pv1b)
    assert node2b
    node3a = ProductNode.objects.create(parent=node2a, obj=ps1)
    assert node3a
    node3b = ProductNode.objects.create(parent=node2b, obj=ps2)
    assert node3b
    node3c = ProductNode.objects.create(parent=node2b, obj=ps3)
    assert node3c

    assert pv1a.cpes == ["cpe:/o:redhat:enterprise_linux:9"]
    assert sorted(p1.cpes) == [
        "cpe:/o:redhat:Brantabernicla:8",
        "cpe:/o:redhat:enterprise_linux:9",
    ]


def test_product_taxonomic_queries():
    p1, ps1, ps2, ps3, pv1a, pv1b = create_product_hierarchy()

    relate_product_hierarchy(p1, ps1, ps2, ps3, pv1a, pv1b)

    assert p1.product_streams == ["Ansercanagicus", "Ansercaerulescens", "Brantabernicla"]
    pv1b.save_product_taxonomy()
    assert pv1b.product_streams == ["Ansercaerulescens", "Brantabernicla"]
    assert pv1b.products == ["RHEL"]
    ps3.save_product_taxonomy()
    assert ps3.products == ["RHEL"]


def relate_product_hierarchy(p1, ps1, ps2, ps3, pv1a, pv1b):
    pvar1 = ProductVariantFactory(name="Base8-test")
    node1 = ProductNode.objects.create(parent=None, obj=p1)
    assert node1
    node2a = ProductNode.objects.create(parent=node1, obj=pv1a)
    assert node2a
    node2b = ProductNode.objects.create(parent=node1, obj=pv1b)
    assert node2b
    node3a = ProductNode.objects.create(parent=node2a, obj=ps1)
    assert node3a
    node3b = ProductNode.objects.create(parent=node2b, obj=ps2)
    assert node3b
    node3c = ProductNode.objects.create(parent=node2b, obj=ps3)
    assert node3c
    node4a = ProductNode.objects.create(parent=node3c, obj=pvar1)
    assert node4a

    p1.save_product_taxonomy()


def create_product_hierarchy():
    p1 = ProductFactory(name="RHEL")
    pv1a = ProductVersionFactory(name="RHEL-7", version="7")
    pv1b = ProductVersionFactory(name="RHEL-8", version="8")
    ps1 = ProductStreamFactory(name="Ansercanagicus")
    ps2 = ProductStreamFactory(name="Ansercaerulescens")
    ps3 = ProductStreamFactory(name="Brantabernicla", cpe="cpe:/o:redhat:Brantabernicla:8")
    return p1, ps1, ps2, ps3, pv1a, pv1b


def test_component_model():
    c1 = ComponentFactory(name="curl", type=Component.Type.SRPM)
    assert Component.objects.get(name="curl") == c1


def test_component_provides():
    upstream = ComponentFactory(type=Component.Type.UPSTREAM)
    upstream_node, _ = upstream.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    dev_comp = ComponentFactory(name="dev", type=Component.Type.NPM)
    dev_comp.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV, parent=upstream_node
    )
    assert dev_comp.purl in upstream.get_provides()


def test_software_build_model():
    sb1 = SoftwareBuildFactory(
        type=SoftwareBuild.Type.BREW,
        meta_attr={"build_id": 9999, "a": 1, "b": 2, "brew_tags": ["RHSA-123-123"]},
    )
    assert SoftwareBuild.objects.get(build_id=sb1.build_id) == sb1
    c1 = ComponentFactory(type=Component.Type.RPM, name="curl", software_build=sb1)
    assert Component.objects.get(name="curl") == c1


def test_get_roots():
    srpm = ComponentFactory(type=Component.Type.SRPM)
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    rpm = ComponentFactory(type=Component.Type.RPM)
    rpm_cnode, _ = rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=srpm_cnode
    )
    assert rpm.get_roots == [srpm_cnode]
    assert srpm.get_roots == [srpm_cnode]

    nested = ComponentFactory()
    nested.cnodes.get_or_create(type=ComponentNode.ComponentNodeType.PROVIDES, parent=rpm_cnode)
    assert nested.get_roots == [srpm_cnode]

    container = ComponentFactory(type=Component.Type.CONTAINER_IMAGE, arch="noarch")
    container_cnode, _ = container.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    container_rpm = ComponentFactory(type=Component.Type.RPM)
    container_rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=container_cnode
    )
    assert not container_rpm.get_roots
    assert container.get_roots == [container_cnode]

    container_source = ComponentFactory(type=Component.Type.UPSTREAM)
    container_source_cnode, _ = container_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=container_cnode
    )
    assert container_source.get_roots == [container_cnode]
    assert container_source.get_roots == [container_cnode]
    container_nested = ComponentFactory(type=Component.Type.NPM)
    container_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=container_source_cnode
    )
    assert container_nested.get_roots == [container_cnode]


def test_product_component_relations():
    build_id = 1754635
    sb = SoftwareBuildFactory(build_id=build_id)
    p1, ps1, ps2, ps3, pv1a, pv1b = create_product_hierarchy()
    relate_product_hierarchy(p1, ps1, ps2, ps3, pv1a, pv1b)
    ProductComponentRelation.objects.create(
        type=ProductComponentRelation.Type.COMPOSE, product_ref=ps1.name, build_id=build_id
    )
    srpm = ComponentFactory(software_build=sb, type=Component.Type.SRPM)
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    srpm.save_product_taxonomy()
    assert ps1.ofuri in srpm.product_streams


def test_get_upstream():
    srpm = ComponentFactory(type=Component.Type.SRPM)
    srpm_cnode, _ = srpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    rpm = ComponentFactory(type=Component.Type.RPM)
    rpm_cnode, _ = rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=srpm_cnode
    )
    srpm_upstream = ComponentFactory(type=Component.Type.UPSTREAM)
    srpm_upstream_cnode, _ = srpm_upstream.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=srpm_cnode
    )
    assert rpm.get_upstreams() == [srpm_upstream.purl]

    container = ComponentFactory(type=Component.Type.CONTAINER_IMAGE, arch="noarch")
    container_cnode, _ = container.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    container_rpm = ComponentFactory(type=Component.Type.RPM)
    container_rpm.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=container_cnode
    )
    assert container_rpm.get_upstreams() == []

    container_source = ComponentFactory(name="container_upstream", type=Component.Type.UPSTREAM)
    container_source_cnode, _ = container_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=container_cnode
    )

    container_nested = ComponentFactory(type=Component.Type.NPM)
    container_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=container_source_cnode
    )
    assert container_nested.get_upstreams() == [container_source.purl]

    container_o_source = ComponentFactory(
        name="contain_upstream_other", type=Component.Type.UPSTREAM
    )
    container_o_source_cnode, _ = container_o_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=container_cnode
    )

    container_other_nested = ComponentFactory(type=Component.Type.NPM)
    container_other_nested.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=container_o_source_cnode
    )
    assert container_other_nested.get_upstreams() == [container_o_source.purl]

import json
import logging
from json import JSONDecodeError

import pytest

from corgi.core.files import ProductManifestFile
from corgi.core.models import Component, ComponentNode, ProductComponentRelation
from corgi.web.templatetags.base_extras import provided_relationship

from .conftest import setup_product
from .factories import (
    ComponentFactory,
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
    ProductStreamFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

logger = logging.getLogger()
pytestmark = [
    pytest.mark.unit,
    pytest.mark.django_db(databases=("default", "read_only"), transaction=True),
]


def test_escapejs_on_copyright():
    # actual copyright_text from snappy rpm
    c = ComponentFactory(
        copyright_text="(c) A AEZaO5, (c) ^P&#x27;,AE^UG.2oDS&#x27;x c v KDC(r)xOE@5wZ&#x27;^NyH+c"
        "@^AP6| bV, (c) /axove+xose/7,-1,0,B/frameset&amp;F axuntanza&amp;1,,3 http"
        "://biblio.cesga.es:81/search, (c) /axove+xose/7,-1,0,B/frameset&amp;F axun"
        "tanza&amp;3,,3 http://db.zaq.ne.jp/asp/bbs/jttk_baasc506_1/article/36 http:"
        "//db.zaq.ne.jp/asp/bbs/jttk_baasc506_1/article/37, (c) Ei El, (c) H2 (c), ("
        "c) I UuE, (c) OOUA UuUS1a OEviy, Copyright 2005 and onwards Google Inc., Co"
        "pyright 2005 Google Inc., Copyright 2008 Google Inc., Copyright 2011 Google"
        " Inc., Copyright 2011, Google Inc., Copyright 2011 Martin Gieseking &lt;mar"
        "tin.gieseking@uos.de&gt;, Copyright 2013 Steinar H. Gunderson, Copyright 20"
        "19 Google Inc., copyright by Cornell University, Copyright Depository of El"
        "ectronic Materials, Copyright Issues Marybeth Peters, (c) ^P u E2OroCIyV ^T"
        " C.au, (c) UiL (c)"
    )
    try:
        c.manifest
    except JSONDecodeError as e:
        assert (
            False
        ), f"JSONDecodeError thrown by component with special chars in copyright_text {e}"

    c = ComponentFactory(copyright_text="©, 🄯, ®, ™")
    try:
        c.manifest
    except JSONDecodeError as e:
        assert (
            False
        ), f"JSONDecodeError thrown by component with special chars in copyright_text {e}"


def test_manifests_exclude_source_container():
    """Test that container sources are excluded packages"""
    containers, stream, _ = setup_products_and_rpm_in_containers()
    assert len(containers) == 3

    manifest_str = ProductManifestFile(stream).render_content()
    manifest = json.loads(manifest_str)
    components = manifest["packages"]
    # Two containers, one RPM, and a product are included
    assert len(components) == 4, components
    # The source container is excluded
    for component in components:
        assert not component["name"].endswith("-container-source")


def test_manifests_exclude_bad_golang_components():
    """Test that Golang components with names like ../ are excluded packages"""
    # Remove below when CORGI-428 is resolved
    containers, stream, _ = setup_products_and_rpm_in_containers()
    assert len(containers) == 3

    bad_golang = ComponentFactory(type=Component.Type.GOLANG, name="./api-")
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=containers[0].cnodes.first(),
        purl=bad_golang.purl,
        obj=bad_golang,
    )
    # Link the bad_golang component to its parent container
    containers[0].save_component_taxonomy()
    assert containers[0].provides.filter(name=bad_golang.name).exists()

    manifest_str = ProductManifestFile(stream).render_content()
    manifest = json.loads(manifest_str)
    components = manifest["packages"]
    # Two containers, one RPM, and a product are included
    assert len(components) == 4, components
    # The golang component is excluded
    for component in components:
        assert not component["name"] == bad_golang.name


def test_stream_manifest_backslash():
    """Test that a tailing backslash in a purl doesn't break rendering"""

    stream = ProductStreamFactory()
    sb = SoftwareBuildFactory()
    component = SrpmComponentFactory(version="2.8.0 \\", software_build=sb)
    component.productstreams.add(stream)

    try:
        stream.manifest
    except JSONDecodeError:
        assert False


def test_component_manifest_backslash():
    """Test that a backslash in a version doesn't break rendering via download_url or related_url"""
    component = ComponentFactory(
        version="\\9.0.6.v20130930",
        type=Component.Type.MAVEN,
        name="org.eclipse.jetty/jetty-webapp",
    )
    assert component.download_url
    try:
        component.manifest
    except JSONDecodeError:
        assert False


def test_slim_rpm_in_containers_manifest():
    containers, stream, rpm_in_container = setup_products_and_rpm_in_containers()

    # Two components linked to this product
    # plus a source container which is shown in API but not in manifests
    released_components = stream.components.manifest_components()
    num_components = len(released_components)
    assert num_components == 2, released_components

    provided = set()
    # Each component has 1 node each
    for released_component in released_components:
        component_nodes = released_component.get_provides_nodes()
        assert len(component_nodes) == 1
        provided.update(component_nodes)

    # Here we are checking that the components share a single provided component
    num_provided = len(provided)
    assert num_provided == 1

    distinct_provides = stream.provides_queryset
    assert len(distinct_provides) == num_provided

    stream_manifest = stream.manifest
    manifest = json.loads(stream_manifest)

    # One package for each component
    # One (and only one) package for each distinct provided
    # Plus one package for the stream itself
    assert len(manifest["packages"]) == num_components + num_provided + 1

    # For each provided in component, one "provided contained by component"
    # For each provided in component, one "provided contains none" indicating a leaf node
    # For each component, one "component is package of product" relationship
    # Plus one "document describes product" relationship for the whole document at the end
    assert len(manifest["relationships"]) == num_components + (num_provided + 1) + 1

    provided_uuid = provided.pop()
    component = containers[0]
    if containers[0].uuid > containers[1].uuid:
        component = containers[1]

    document_describes_product = {
        "relatedSpdxElement": f"SPDXRef-{stream.uuid}",
        "relationshipType": "DESCRIBES",
        "spdxElementId": "SPDXRef-DOCUMENT",
    }

    component_is_package_of_product = {
        "relatedSpdxElement": f"SPDXRef-{stream.uuid}",
        "relationshipType": "PACKAGE_OF",
        "spdxElementId": f"SPDXRef-{component.uuid}",
    }

    provided_contained_by_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "CONTAINED_BY",
        "spdxElementId": f"SPDXRef-{provided_uuid}",
    }

    assert manifest["relationships"][0] == provided_contained_by_component
    assert manifest["relationships"][1] == component_is_package_of_product
    assert manifest["relationships"][-1] == document_describes_product


def test_product_manifest_excludes_unreleased_components():
    """Test that manifests for products don't include unreleased components"""
    component, stream, provided, dev_provided = setup_products_and_components_provides(
        released=False
    )

    manifest = json.loads(stream.manifest)

    # No released components linked to this product
    num_components = len(stream.components.manifest_components())
    assert num_components == 0

    num_provided = len(stream.provides_queryset)
    assert num_provided == 0

    # Manifest contains info for no components, no provides, and only the product itself
    assert len(manifest["packages"]) == 1

    # Only "component" is actually the product
    product_data = manifest["packages"][0]

    assert product_data["SPDXID"] == f"SPDXRef-{stream.uuid}"
    assert product_data["name"] == stream.name
    for index, cpe in enumerate(stream.cpes):
        assert product_data["externalRefs"][index]["referenceLocator"] == cpe

    # Only one "document describes product" relationship for the whole document at the end
    assert len(manifest["relationships"]) == 1

    assert manifest["relationships"][0] == {
        "relatedSpdxElement": f"SPDXRef-{stream.uuid}",
        "relationshipType": "DESCRIBES",
        "spdxElementId": "SPDXRef-DOCUMENT",
    }


def test_product_manifest_excludes_internal_components():
    """Test that manifests for products don't include unreleased components"""
    component, stream, provided, dev_provided = setup_products_and_components_provides(
        internal_component=True
    )

    manifest = json.loads(stream.manifest)

    # This is the root rpm src component
    num_components = len(stream.components.manifest_components())
    assert num_components == 1

    num_provided = len(stream.provides_queryset)
    assert num_provided == 1

    package_data = manifest["packages"]
    for package in package_data:
        assert "redhat.com/" not in package["name"]


def test_product_manifest_properties():
    """Test that all models inheriting from ProductModel have a .manifest property
    And that it generates valid JSON."""
    component, stream, provided, dev_provided = setup_products_and_components_provides()

    manifest = json.loads(stream.manifest)

    # One component linked to this product
    num_components = len(stream.components.manifest_components())
    assert num_components == 1

    num_provided = len(stream.provides_queryset)
    assert num_provided == 2

    # Manifest contains info for all components, their provides, and the product itself
    assert len(manifest["packages"]) == num_components + num_provided + 1

    # Last "component" is actually the product
    component_data = manifest["packages"][0]
    product_data = manifest["packages"][-1]

    # UUID and PURL, for each component attached to product, should be included in manifest
    assert component_data["SPDXID"] == f"SPDXRef-{component.uuid}"
    assert component_data["name"] == component.name
    assert component_data["externalRefs"][0]["referenceLocator"] == component.purl

    assert product_data["SPDXID"] == f"SPDXRef-{stream.uuid}"
    assert product_data["name"] == stream.name
    for index, cpe in enumerate(stream.cpes):
        assert product_data["externalRefs"][index]["referenceLocator"] == cpe

    document_describes_product = {
        "relatedSpdxElement": f"SPDXRef-{stream.uuid}",
        "relationshipType": "DESCRIBES",
        "spdxElementId": "SPDXRef-DOCUMENT",
    }

    component_is_package_of_product = {
        "relatedSpdxElement": f"SPDXRef-{stream.uuid}",
        "relationshipType": "PACKAGE_OF",
        "spdxElementId": f"SPDXRef-{component.uuid}",
    }

    provided_contained_by_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "CONTAINED_BY",
        "spdxElementId": f"SPDXRef-{provided.uuid}",
    }

    dev_provided_dependency_of_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "DEV_DEPENDENCY_OF",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    # For each provided in component, one "provided contained by component"
    # For each provided in component, one "provided contains none" indicating a leaf node
    # For each component, one "component is package of product" relationship
    # Plus one "document describes product" relationship for the whole document at the end
    assert len(manifest["relationships"]) == num_components + num_provided + 1

    if dev_provided.uuid < provided.uuid:
        dev_provided_index = 0
        provided_index = 1
    else:
        provided_index = 0
        dev_provided_index = 1

    assert manifest["relationships"][provided_index] == provided_contained_by_component
    assert manifest["relationships"][dev_provided_index] == dev_provided_dependency_of_component
    assert manifest["relationships"][num_provided] == component_is_package_of_product
    assert manifest["relationships"][-1] == document_describes_product


def test_no_duplicates_in_manifest_with_upstream():
    stream, component, other_component, upstream = setup_products_and_components_upstreams()

    assert component.upstreams.get(pk=upstream.uuid)
    assert other_component.upstreams.get(pk=upstream.uuid)

    # 1 (product) + 2 (root components) + 1 (upstream)
    root_components = stream.components.manifest_components().order_by("software_build__build_id")
    assert len(root_components) == 2
    assert root_components.first() == component
    assert root_components.last() == other_component

    upstream_components = stream.upstreams_queryset
    assert len(upstream_components) == 1
    assert upstream_components.first() == upstream

    manifest = json.loads(stream.manifest)
    assert len(manifest["packages"]) == 4


def test_component_manifest_properties():
    """Test that all Components have a .manifest property
    And that it generates valid JSON."""
    component, _, provided, dev_provided = setup_products_and_components_provides()

    manifest = json.loads(component.manifest)

    num_provided = len(component.get_provides_nodes())

    assert num_provided == 2

    document_describes_product = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "DESCRIBES",
        "spdxElementId": "SPDXRef-DOCUMENT",
    }

    provided_contained_by_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "CONTAINED_BY",
        "spdxElementId": f"SPDXRef-{provided.uuid}",
    }

    dev_provided_dependency_of_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "DEV_DEPENDENCY_OF",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    assert len(manifest["relationships"]) == num_provided + 1
    # Relationships in manifest use a constant ordering based on object UUID
    # Actual UUIDs created in the tests will vary, so make sure we assert the right thing
    if dev_provided.uuid < provided.uuid:
        dev_provided_index = 0
        provided_index = 1
    else:
        provided_index = 0
        dev_provided_index = 1

    assert manifest["relationships"][provided_index] == provided_contained_by_component
    assert manifest["relationships"][dev_provided_index] == dev_provided_dependency_of_component
    assert manifest["relationships"][-1] == document_describes_product


def setup_products_and_components_upstreams():
    stream, variant = setup_product()
    meta_attr = {"released_errata_tags": ["RHBA-2023:1234"]}

    build = SoftwareBuildFactory(
        build_id=1,
        meta_attr=meta_attr,
    )

    other_build = SoftwareBuildFactory(
        build_id=2,
        meta_attr=meta_attr,
    )

    upstream = ComponentFactory(namespace=Component.Namespace.UPSTREAM)
    component = ComponentFactory(
        software_build=build, type=Component.Type.CONTAINER_IMAGE, arch="noarch"
    )
    other_component = ComponentFactory(
        software_build=other_build, type=Component.Type.CONTAINER_IMAGE, arch="noarch"
    )
    cnode = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=component.purl, obj=component
    )
    other_cnode = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=other_component.purl,
        obj=other_component,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=cnode,
        purl=upstream.purl,
        obj=upstream,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=other_cnode,
        purl=upstream.purl,
        obj=upstream,
    )
    # Link the components to each other
    component.save_component_taxonomy()
    other_component.save_component_taxonomy()

    # The product_ref here is a variant name but below we use it's parent stream
    # to generate the manifest
    ProductComponentRelationFactory(
        build_id=str(build.build_id),
        build_type=build.build_type,
        product_ref=variant.name,
        type=ProductComponentRelation.Type.ERRATA,
    )
    ProductComponentRelationFactory(
        build_id=str(other_build.build_id),
        build_type=other_build.build_type,
        product_ref=variant.name,
        type=ProductComponentRelation.Type.ERRATA,
    )
    # Link the components to the ProductModel instances
    build.save_product_taxonomy()
    other_build.save_product_taxonomy()

    return stream, component, other_component, upstream


def setup_products_and_components_provides(released=True, internal_component=False):
    stream, variant = setup_product()
    meta_attr = {"released_errata_tags": []}
    if released:
        meta_attr["released_errata_tags"] = ["RHBA-2023:1234"]
    build = SoftwareBuildFactory(
        build_id=1,
        meta_attr=meta_attr,
    )
    if internal_component:
        provided = ComponentFactory(name="gitlab.cee.redhat.com/", type=Component.Type.GOLANG)
        dev_provided = ComponentFactory(name="github.com/blah.redhat.com", type=Component.Type.NPM)
    else:
        provided = ComponentFactory(type=Component.Type.RPM, arch="x86_64")
        dev_provided = ComponentFactory(type=Component.Type.NPM)
    component = SrpmComponentFactory(
        software_build=build,
    )
    cnode = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=component.purl, obj=component
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=cnode,
        purl=provided.purl,
        obj=provided,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES_DEV,
        parent=cnode,
        purl=dev_provided.purl,
        obj=dev_provided,
    )
    # Link the components to each other
    component.save_component_taxonomy()

    # The product_ref here is a variant name but below we use it's parent stream
    # to generate the manifest
    ProductComponentRelationFactory(
        build_id=str(build.build_id),
        build_type=build.build_type,
        product_ref=variant.name,
        type=ProductComponentRelation.Type.ERRATA,
    )
    # Link the components to the ProductModel instances
    build.save_product_taxonomy()
    return component, stream, provided, dev_provided


def setup_products_and_rpm_in_containers():
    stream, variant = setup_product()
    rpm_in_container = ComponentFactory(type=Component.Type.RPM, arch="x86_64")
    containers = []
    for name in ["", "", "some-container-source"]:
        build, container = _build_rpm_in_containers(rpm_in_container, name=name)

        # The product_ref here is a variant name but below we use it's parent stream
        # to generate the manifest
        ProductComponentRelationFactory(
            build_id=str(build.build_id),
            build_type=build.build_type,
            product_ref=variant.name,
            type=ProductComponentRelation.Type.ERRATA,
        )
        # Link the components to the ProductModel instances
        build.save_product_taxonomy()
        containers.append(container)
    return containers, stream, rpm_in_container


def _build_rpm_in_containers(rpm_in_container, name=""):
    build = SoftwareBuildFactory(
        meta_attr={"released_errata_tags": ["RHBA-2023:1234"]},
    )
    if not name:
        container = ContainerImageComponentFactory(
            software_build=build,
        )
    else:
        container = ContainerImageComponentFactory(
            name=name,
            software_build=build,
        )
    cnode = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=container.purl, obj=container
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=cnode,
        purl=rpm_in_container.purl,
        obj=rpm_in_container,
    )
    # Link the components to each other
    container.save_component_taxonomy()
    return build, container


def test_provided_relationships():
    """Test that nodes / components in a manifest are linked to each other
    using the correct relationship type"""
    # Arch-specific containers are a VARIANT_OF the parent noarch container
    # Regardless of node type
    purl = f"pkg:{Component.Type.CONTAINER_IMAGE.lower()}/name@version"
    node_type = ComponentNode.ComponentNodeType.PROVIDES
    assert provided_relationship(purl, node_type) == "VARIANT_OF"
    node_type = ComponentNode.ComponentNodeType.PROVIDES_DEV
    assert provided_relationship(purl, node_type) == "VARIANT_OF"

    # All other component types are either a DEV_DEPENDENCY_OF their parent
    purl = f"pkg:{Component.Type.RPM.lower()}/name@version"
    assert provided_relationship(purl, node_type) == "DEV_DEPENDENCY_OF"
    purl = f"pkg:{Component.Type.GOLANG.lower()}/name@version"
    assert provided_relationship(purl, node_type) == "DEV_DEPENDENCY_OF"

    # Or CONTAINED_BY their parent, depending on node type
    node_type = ComponentNode.ComponentNodeType.PROVIDES
    assert provided_relationship(purl, node_type) == "CONTAINED_BY"
    purl = f"pkg:{Component.Type.RPM.lower()}/name@version"
    assert provided_relationship(purl, node_type) == "CONTAINED_BY"

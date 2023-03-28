import json
import logging
from datetime import datetime
from json import JSONDecodeError

import pytest

from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductNode,
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


def test_latest_components_exclude_source_container():
    """Test that container sources are excluded packages"""
    containers, stream, _ = setup_products_and_rpm_in_containers()
    assert len(containers) == 3

    components = stream.get_latest_components()
    assert len(components) == 2
    assert not components.first().name.endswith("-container-source")


def test_manifest_backslash():
    """Test that a tailing backslash in a purl doesn't break rendering"""

    stream = ProductStreamFactory()
    sb = SoftwareBuildFactory(
        completion_time=datetime.strptime("2017-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z")
    )
    component = SrpmComponentFactory(version="2.8.0 \\", software_build=sb)
    component.productstreams.add(stream)

    try:
        stream.manifest
    except JSONDecodeError:
        assert False


def test_slim_rpm_in_containers_manifest():
    containers, stream, rpm_in_container = setup_products_and_rpm_in_containers()

    # Two components linked to this product
    num_components = len(stream.get_latest_components())
    assert num_components == 2

    provided = set()
    # Each component has 1 node each
    for latest_component in stream.get_latest_components():
        component_nodes = latest_component.get_provides_nodes()
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


def test_product_manifest_properties():
    """Test that all models inheriting from ProductModel have a .manifest property
    And that it generates valid JSON."""
    component, stream, provided, dev_provided = setup_products_and_components_provides()

    manifest = json.loads(stream.manifest)

    # One component linked to this product
    num_components = len(stream.get_latest_components())
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
    if stream.cpes:
        assert product_data["externalRefs"][0]["referenceLocator"] == stream.cpes[0]
    assert product_data["externalRefs"][-1]["referenceLocator"] == f"cpe:/{stream.ofuri}"

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


def setup_products_and_components_provides():
    stream, variant = setup_product()
    build = SoftwareBuildFactory(
        build_id=1,
        completion_time=datetime.strptime("2017-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z"),
    )
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
            product_ref=variant.name,
            type=ProductComponentRelation.Type.ERRATA,
        )
        # Link the components to the ProductModel instances
        build.save_product_taxonomy()
        containers.append(container)
    return containers, stream, rpm_in_container


def _build_rpm_in_containers(rpm_in_container, name=""):
    build = SoftwareBuildFactory(
        completion_time=datetime.strptime("2017-03-29 12:13:29 GMT+0000", "%Y-%m-%d %H:%M:%S %Z%z"),
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


def setup_product():
    product = ProductFactory()
    version = ProductVersionFactory(products=product)
    stream = ProductStreamFactory(products=product, productversions=version)
    variant = ProductVariantFactory(
        name="1", products=product, productversions=version, productstreams=stream
    )
    # TODO: Factory should probably create nodes for model
    #  Move these somewhere for reuse - common.py helper method, or fixtures??
    pnode = ProductNode.objects.create(parent=None, obj=product)
    pvnode = ProductNode.objects.create(parent=pnode, obj=version)
    psnode = ProductNode.objects.create(parent=pvnode, obj=stream)
    ProductNode.objects.create(parent=psnode, obj=variant)
    # This generates and saves the ProductModel properties of stream
    # AKA we link the ProductModel instances to each other
    stream.save_product_taxonomy()
    assert variant in stream.productvariants.get_queryset()
    return stream, variant

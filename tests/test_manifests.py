import json
import logging
from datetime import datetime
from json import JSONDecodeError

import jsonschema
import pytest

from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductNode,
)

from .factories import (
    ComponentFactory,
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


def test_product_manifest_properties():
    """Test that all models inheriting from ProductModel have a .manifest property
    And that it generates valid JSON."""
    component, stream, provided, dev_provided = setup_products_and_components()

    manifest = json.loads(stream.manifest)

    # From https://raw.githubusercontent.com/spdx/spdx-spec/development/
    # v2.2.2/schemas/spdx-schema.json
    with open("tests/data/spdx-22-spec.json", "r") as spec_file:
        schema = json.load(spec_file)
    jsonschema.validate(manifest, schema)

    # One component linked to this product
    num_components = len(stream.get_latest_components())
    assert num_components == 1

    num_provided = 0
    for latest_component in stream.get_latest_components():
        num_provided += latest_component.get_provides_nodes().count()

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

    provided_contains_nothing = {
        "relatedSpdxElement": "NONE",
        "relationshipType": "CONTAINS",
        "spdxElementId": f"SPDXRef-{provided.uuid}",
    }

    dev_provided_dependency_of_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "DEV_DEPENDENCY_OF",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    dev_provided_contains_nothing = {
        "relatedSpdxElement": "NONE",
        "relationshipType": "CONTAINS",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    # For each provided in component, one "provided contained by component"
    # For each provided in component, one "provided contains none" indicating a leaf node
    # For each component, one "component is package of product" relationship
    # Plus one "document describes product" relationship for the whole document at the end
    assert len(manifest["relationships"]) == num_components + (num_provided * 2) + 1
    assert manifest["relationships"][0] == provided_contained_by_component
    assert manifest["relationships"][1] == provided_contains_nothing
    assert manifest["relationships"][2] == dev_provided_dependency_of_component
    assert manifest["relationships"][3] == dev_provided_contains_nothing
    assert manifest["relationships"][-2] == component_is_package_of_product
    assert manifest["relationships"][-1] == document_describes_product


def test_component_manifest_properties():
    """Test that all Components have a .manifest property
    And that it generates valid JSON."""
    component, _, provided, dev_provided = setup_products_and_components()

    manifest = json.loads(component.manifest)

    # From https://raw.githubusercontent.com/spdx/spdx-spec/development/
    # v2.2.2/schemas/spdx-schema.json
    with open("tests/data/spdx-22-spec.json", "r") as spec_file:
        schema = json.load(spec_file)
    jsonschema.validate(manifest, schema)

    num_provided = component.get_provides_nodes().count()

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

    provided_contains_nothing = {
        "relatedSpdxElement": "NONE",
        "relationshipType": "CONTAINS",
        "spdxElementId": f"SPDXRef-{provided.uuid}",
    }

    dev_provided_dependency_of_component = {
        "relatedSpdxElement": f"SPDXRef-{component.uuid}",
        "relationshipType": "DEV_DEPENDENCY_OF",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    dev_provided_contains_nothing = {
        "relatedSpdxElement": "NONE",
        "relationshipType": "CONTAINS",
        "spdxElementId": f"SPDXRef-{dev_provided.uuid}",
    }

    assert len(manifest["relationships"]) == (num_provided * 2) + 1
    assert manifest["relationships"][0] == provided_contained_by_component
    assert manifest["relationships"][1] == provided_contains_nothing
    assert manifest["relationships"][2] == dev_provided_dependency_of_component
    assert manifest["relationships"][3] == dev_provided_contains_nothing
    assert manifest["relationships"][-1] == document_describes_product


def setup_products_and_components():
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

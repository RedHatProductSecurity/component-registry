import json
import logging

import pytest

from corgi.core.models import ComponentNode, ProductComponentRelation, ProductNode

from .factories import (
    ComponentFactory,
    ProductComponentRelationFactory,
    ProductFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    ProductVersionFactory,
    SoftwareBuildFactory,
)

logger = logging.getLogger()
pytestmark = pytest.mark.unit


def test_product_manifest_properties():
    """Test that all models inheriting from ProductModel have a .manifest property
    And that it generates valid JSON. TODO: Use a library to generate + validate the SPDX data"""
    product = ProductFactory(product_variants=["1"])
    version = ProductVersionFactory(product_variants=["1"])
    stream = ProductStreamFactory(product_variants=["1"])
    variant = ProductVariantFactory(name="1")
    pnode = ProductNode.objects.create(parent=None, obj=product)
    pvnode = ProductNode.objects.create(parent=pnode, obj=version)
    psnode = ProductNode.objects.create(parent=pvnode, obj=stream)
    _ = ProductNode.objects.create(parent=psnode, obj=variant)

    build = SoftwareBuildFactory(build_id=1)
    component = ComponentFactory(software_build=build)
    _, _ = component.cnodes.get_or_create(type=ComponentNode.ComponentNodeType.SOURCE, parent=None)
    ProductComponentRelationFactory(
        build_id="1", product_ref="1", type=ProductComponentRelation.Type.ERRATA
    )

    build.save_component_taxonomy()
    build.save_product_taxonomy()

    json.loads(product.manifest)
    json.loads(version.manifest)
    json.loads(stream.manifest)
    manifest = json.loads(variant.manifest)
    # Test will fail with JSONDecodeError if above isn't valid
    # Eventually, we should also check the actual manifest content is valid SPDX data
    # Then most of below can go away

    # One component linked to this product
    num_components = len(variant.components)
    assert num_components == 1
    # Manifest contains info for all components + the product itself
    assert len(manifest["packages"]) == 2

    # Last "component" is actually the product
    component_data = manifest["packages"][0]
    product_data = manifest["packages"][-1]

    # UUID and PURL, for each component attached to product, should be included in manifest
    assert component_data["SPDXID"] == f"SPDXRef-{component.uuid}"
    assert component_data["name"] == component.name
    assert component_data["externalRefs"][0]["referenceLocator"] == component.purl

    assert product_data["SPDXID"] == f"SPDXRef-{variant.uuid}"
    assert product_data["name"] == variant.name
    if variant.cpes:
        assert product_data["externalRefs"][0]["referenceLocator"] == variant.cpes[0]
    assert product_data["externalRefs"][-1]["referenceLocator"] == f"cpe:/{variant.ofuri}"

    document_describes_product = {
        "relatedSpdxElement": f"SPDXRef-{variant.uuid}",
        "relationshipType": "DESCRIBES",
        "spdxElementId": "SPDXRef-DOCUMENT",
    }

    component_is_package_of_product = {
        "relatedSpdxElement": f"SPDXRef-{variant.uuid}",
        "relationshipType": "PACKAGE_OF",
        "spdxElementId": f"SPDXRef-{component.uuid}",
    }

    component_contains_nothing = {
        "relatedSpdxElement": "NONE",
        "relationshipType": "CONTAINS",
        "spdxElementId": f"SPDXRef-{component.uuid}",
    }

    # For each component, one "component is package of product" relationship
    # And one "component contains nothing" relationship
    # Plus one "document describes product" relationship for the whole document at the end
    assert len(manifest["relationships"]) == (num_components * 2) + 1
    assert manifest["relationships"][0] == component_is_package_of_product
    assert manifest["relationships"][1] == component_contains_nothing
    assert manifest["relationships"][-1] == document_describes_product

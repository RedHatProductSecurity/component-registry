import logging
from io import StringIO

import pytest
from spdx.parsers import jsonparser
from spdx.parsers.jsonyamlxmlbuilders import Builder
from spdx.parsers.loggers import FileLogger

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
)

# from spdx.parsers.loggers import StandardLogger
# from spdx.parsers.tagvalue import Parser
# from spdx.parsers.tagvaluebuilders import Builder


logger = logging.getLogger()
pytestmark = pytest.mark.unit


def test_product_manifest_properties():
    """Test that all models inheriting from ProductModel have a .manifest property
    And that it generates valid JSON. TODO: Use a library to generate + validate the SPDX data"""
    product = ProductFactory()
    version = ProductVersionFactory()
    stream = ProductStreamFactory()
    variant = ProductVariantFactory(name="1")
    pnode = ProductNode.objects.create(parent=None, obj=product, object_id=product.pk)
    pvnode = ProductNode.objects.create(parent=pnode, obj=version, object_id=version.pk)
    psnode = ProductNode.objects.create(parent=pvnode, obj=stream, object_id=stream.pk)
    _ = ProductNode.objects.create(parent=psnode, obj=variant, object_id=variant.pk)
    # This generates and saves the product_variants property of stream
    stream.save_product_taxonomy()
    assert variant.name in stream.product_variants

    build = SoftwareBuildFactory(build_id=1)
    build.save()
    provided = ComponentFactory(type="RPM")
    component = ComponentFactory(
        software_build=build,
        type="SRPM",
        product_variants=[variant.ofuri],
        product_streams=[stream.ofuri],
    )
    component.save()
    cnode, _ = component.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None, purl=component.purl
    )
    provided.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES, parent=cnode, purl=provided.purl
    )
    # The product_ref here is a variant name but below we use it's parent stream
    # to generate the manifest
    ProductComponentRelationFactory(
        build_id="1", product_ref="1", type=ProductComponentRelation.Type.ERRATA
    )

    build.save_component_taxonomy()
    build.save_product_taxonomy()

    component = Component.objects.get(pk=component.uuid)
    assert len(component.provides) == 1
    print(stream.manifest)
    # assert False
    err = StringIO()
    parser = jsonparser.Parser(Builder(), FileLogger(err))
    manifest_io = StringIO(stream.manifest)
    doc, _ = parser.parse(manifest_io)

    assert len(doc.packages) == 2

    pkg_verif_code_node_error = "'None' is not a valid value for PKG_VERIF_CODE"
    pkg_errors = []
    for _ in range(len(doc.packages)):
        pkg_errors.append(pkg_verif_code_node_error)
    known_errors = "\n".join(pkg_errors)
    errors = err.getvalue().strip()
    assert errors == known_errors

    # TODO add this once https://github.com/spdx/tools-python/issues/193 is fixed
    # for package in doc.packages:
    #     assert not package.are_files_analyzed

    assert len(doc.relationships) == 1

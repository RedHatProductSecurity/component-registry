import json

import pytest

from corgi.collectors.pnc import SbomerSbom
from corgi.core.models import SoftwareBuild

pytestmark = pytest.mark.unit


def test_validate_sbom():
    # Tests are temporary, pending completion of CORGI-488

    # Test a valid sbom
    with open("tests/data/pnc/pnc_sbom.json") as sbom_file:
        sbom_data = json.load(sbom_file)

    sbom = SbomerSbom(sbom_data)

    for bomref, component in sbom.components.items():
        # All Red Hat components should have PNC or Brew info
        if "redhat" in component["purl"]:
            assert "build_id" in component
            assert component["build_id"]["type"] in [
                SoftwareBuild.Type.PNC,
                SoftwareBuild.Type.BREW,
            ]

    assert len(sbom.components) == 6

    # Check that Brew builds are handled correctly
    # A component with only a Brew build uses it as the primary build
    assert sbom.components[
        "pkg:maven/org.jboss/jboss-transaction-spi@7.6.0.Final-redhat-1?type=jar"
    ]["build_id"] == {
        "type": SoftwareBuild.Type.BREW,
        "id": "1234567890",
    }

    # A component with both PNC and Brew builds stores Brew info in meta
    assert sbom.components["pkg:maven/io.smallrye.reactive/mutiny@1.7.0.redhat-00001?type=jar"][
        "build_id"
    ] == {
        "type": SoftwareBuild.Type.PNC,
        "id": "AUOMUWXT3VQAA",
    }
    assert (
        sbom.components["pkg:maven/io.smallrye.reactive/mutiny@1.7.0.redhat-00001?type=jar"][
            "meta_attr"
        ]["brew_build_id"]
        == "0987654321"
    )

    # An sbom with no components
    with pytest.raises(ValueError):
        with open("tests/data/pnc/pnc_sbom_no_components.json") as sbom_file:
            sbom_data = json.load(sbom_file)

        sbom = SbomerSbom(sbom_data)

    # An sbom where a component is missing build info
    with pytest.raises(ValueError):
        with open("tests/data/pnc/pnc_sbom_no_build_info.json") as sbom_file:
            sbom_data = json.load(sbom_file)

        sbom = SbomerSbom(sbom_data)

    # An sbom with an unknown build type
    with pytest.raises(ValueError):
        with open("tests/data/pnc/pnc_sbom_bad_build_info.json") as sbom_file:
            sbom_data = json.load(sbom_file)

        sbom = SbomerSbom(sbom_data)

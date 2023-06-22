import json

import pytest

from corgi.collectors.pnc import parse_pnc_sbom

pytestmark = pytest.mark.unit


def test_validate_sbom():
    # Tests are temporary, pending completion of CORGI-488

    # Test a valid sbom
    with open("tests/data/pnc/pnc_sbom.json") as sbom_file:
        sbom_data = json.load(sbom_file)

    component_count = parse_pnc_sbom(sbom_data)
    assert component_count == 4

    # An sbom with no components
    with pytest.raises(ValueError):
        with open("tests/data/pnc/pnc_sbom_no_components.json") as sbom_file:
            sbom_data = json.load(sbom_file)

        parse_pnc_sbom(sbom_data)

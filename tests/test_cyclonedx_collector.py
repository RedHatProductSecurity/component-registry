import os

import pytest

from corgi.collectors.cyclonedx import CycloneDxSbom

test_sboms = {
    "camel": "camel-quarkus-2.13.7.Final-redhat-00003-sbom.json",
    "optaplanner": "optaplanner-quarkus-2.13.7.Final-redhat-00003-sbom.json",
    "qpid": "qpid-jms-client-quarkus-2.13.7.Final-redhar-00003-sbom.json",
    "quarkus": "quarkus-2.13.7.Final-redhat-00003-sbom.json",
}

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("test_sbom", test_sboms.values())
def test_parse_sbom(test_sbom):
    results = CycloneDxSbom.parse_file(os.path.join("tests/data/middleware/", test_sbom))

    for r in results:
        # Ensure everything has mandatory fields
        assert r["meta"]["group_id"] != ""
        assert r["meta"]["version"] != ""
        assert r["meta"]["name"] != ""


def test_sbom_values():
    """Ensure specific values are read correctly"""
    with open(os.path.join("tests/data/middleware/", test_sboms["quarkus"])) as sbom:
        results = CycloneDxSbom.parse(sbom.read())

    wildfly = list(filter(lambda x: x["meta"]["name"] == "wildfly-common", results))

    assert len(wildfly) == 1
    wildfly = wildfly[0]

    assert wildfly["meta"]["group_id"] == "org.wildfly.common"
    assert wildfly["meta"]["version"] == "1.5.4.Final-format-001-redhat-00001"
    assert wildfly["meta"]["declared_licenses"] == "Apache-2.0"

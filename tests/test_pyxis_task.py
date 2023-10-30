import os
from string import Template
from unittest.mock import patch

import pytest

from corgi.collectors.models import CollectorPyxisNameLabel
from corgi.collectors.pyxis import Pyxis
from corgi.core.models import Component, SoftwareBuild
from corgi.tasks.pyxis import slow_fetch_pyxis_manifest, fetch_repo_mapping_for_nvr
from tests.factories import ProductComponentRelationFactory

pytestmark = pytest.mark.unit


@pytest.mark.django_db
@patch("corgi.tasks.brew.slow_save_taxonomy.delay")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
def test_slow_fetch_pyxis_manifest(sca, taxonomy, requests_mock):
    with open("tests/data/pyxis/manifest.json", "r") as data:
        adapter = requests_mock.post(os.getenv("CORGI_PYXIS_GRAPHQL_URL"), text=data.read())

    image_id = "64dccc5b6d82013739c4f7b8"
    old_relation = ProductComponentRelationFactory(
        build_id=image_id, build_type=SoftwareBuild.Type.PYXIS, software_build=None
    )
    # We create a build and root component the first time we process this manifest
    result = slow_fetch_pyxis_manifest(image_id)
    assert result is True

    image_index = Component.objects.get(
        name="image-controller", type=Component.Type.CONTAINER_IMAGE
    )

    # assert we link the relations to the build
    software_build = SoftwareBuild.objects.get(
        build_id=image_id, build_type=SoftwareBuild.Type.PYXIS
    )
    old_relation.refresh_from_db()
    assert old_relation.software_build == software_build

    # even if we're reprocessing a Pyxis manifest, so the build already exists / wasn't just created
    new_relation = ProductComponentRelationFactory(
        build_id=image_id, build_type=SoftwareBuild.Type.PYXIS, software_build=None
    )
    result = slow_fetch_pyxis_manifest(image_id)
    assert result is False
    new_relation.refresh_from_db()
    assert new_relation.software_build == software_build

    components = {}
    for descendant in image_index.cnodes.get_queryset().get_descendants():
        components[descendant.obj.type] = components.get(descendant.obj.type, [])
        components[descendant.obj.type].append(descendant.purl)

    assert set(components.keys()) == {Component.Type.RPM, Component.Type.GOLANG}
    assert len(components[Component.Type.RPM]) == 104
    assert len(components[Component.Type.GOLANG]) == 627

    # TODO: Check that real license values in Pyxis are sane / match our expectations
    #  For now we just use garbage data to test that we can set them correctly
    assert (
        Component.objects.exclude(license_declared_raw="")
        .values_list("license_declared_raw", flat=True)
        .first()
        == "USE-GARBAGE-DATA OR ELSE-WE-CANT-TEST"
    )

    related_urls_for_rpms = (
        Component.objects.filter(type=Component.Type.RPM)
        .exclude(related_url="")
        .values_list("related_url", flat=True)
        .order_by("name")
    )
    assert len(related_urls_for_rpms) == 2
    # readline specifies only an issue tracker, we just use the first ref
    assert related_urls_for_rpms.first() == "https://bugzilla.redhat.com/"
    # rhel-release specifies an issue-tracker and website, we prefer websites
    assert related_urls_for_rpms.last() == "https://www.redhat.com/"

    # If the same property name appears multiple times, save all the values, not just the first one
    # A little complicated since not every component has multiple CPEs that we need for this test
    pyxis_props = Component.objects.exclude(meta_attr__pyxis_properties=[]).values_list(
        "meta_attr__pyxis_properties", flat=True
    )
    multiple_cpes = ()
    for props in pyxis_props:
        multiple_cpes = tuple(prop["value"] for prop in props if prop["name"] == "syft:cpe23")
        if len(multiple_cpes) > 1:
            # Assert we store all the CPE values in the meta_attr
            assert multiple_cpes[0] != multiple_cpes[-1]
            break
    # And assert at least 1 component had multiple CPEs / we broke out of the loop above
    assert len(multiple_cpes) > 1

    manifest_query = Pyxis().MANIFEST_QUERY
    # The query's text, structure, or included fields may change and this test will still pass
    # But we can at least assert it's called once with the right image ID and other args
    assert adapter.last_request.json() == {
        "query": Template(manifest_query).substitute(manifest_id=image_id, page=0, page_size=50)
    }
    # We can also assert some important fields are in the query
    for field in (
        "incompleteness_reasons {",
        " org_id",
        " creation_date",
        "external_references {",
        "properties {",
        "supplier {",
        "licenses {",
    ):
        assert field in manifest_query

    assert software_build.source
    sca.assert_called_once_with(str(software_build.uuid), force_process=False)
    taxonomy.assert_called_once_with(image_id, SoftwareBuild.Type.PYXIS)


@pytest.mark.django_db
@patch("corgi.tasks.brew.slow_save_taxonomy.delay")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
def test_slow_fetch_empty_pyxis_manifest(sca, taxonomy, requests_mock):
    """Test that we can process a manifest with no repositories"""
    with open("tests/data/pyxis/empty_manifest.json", "r") as data:
        requests_mock.post(os.getenv("CORGI_PYXIS_GRAPHQL_URL"), text=data.read())

    image_id = "64dccc5b6d82013739c4f7b8"
    slow_fetch_pyxis_manifest(image_id)
    assert Component.objects.count() == 0
    assert SoftwareBuild.objects.count() == 0

    # The build / Pyxis manifest doesn't have a source URL for this image
    # We don't call the SCA task in this case to avoid many errors in the monitoring email
    sca.assert_not_called()
    taxonomy.assert_not_called()


def get_nvr_name_label_to_repo_mapping(requests_mock):
    nvr = (
        "ose-cluster-ovirt-csi-operator-container-v4.12.0-202301042354.p0.gfeb14fb.assembly.stream"
    )
    with open("tests/data/pyxis/images_by_nvr.json") as data:
        requests_mock.post(os.getenv("CORGI_PYXIS_GRAPHQL_URL"), text=data.read())
    fetch_repo_mapping_for_nvr(nvr, "openshift/ose-ovirt-csi-driver-operator")
    name_label = CollectorPyxisNameLabel.objects.get(name="openshift/ose-ovirt-csi-driver-operator")
    assert "openshift4/ovirt-csi-driver-rhel8-operator" in name_label.repos.values_list(
        "name", flat=True
    )

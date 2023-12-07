import json
from string import Template
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils.timezone import now

from corgi.collectors.brew import Brew
from corgi.collectors.models import CollectorPyxisImage, CollectorPyxisImageRepository
from corgi.collectors.pyxis import query
from corgi.core.models import Component, SoftwareBuild
from corgi.tasks.pyxis import (
    slow_fetch_pyxis_image_by_nvr,
    slow_fetch_pyxis_manifest,
    slow_update_name_for_container_from_pyxis,
)
from tests.factories import (
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
)

pytestmark = pytest.mark.unit


@pytest.mark.django_db
@patch("corgi.tasks.common.slow_save_taxonomy.delay")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
@patch("corgi.collectors.pyxis.session.post")
def test_slow_fetch_pyxis_manifest(post, sca, taxonomy):
    with open("tests/data/pyxis/manifest.json", "r") as data:
        manifest = json.load(data)
    wrapped_manifest = {"data": {"get_content_manifest": {"data": manifest}}}
    post.return_value.json.return_value = wrapped_manifest

    image_id = "64dccc5b6d82013739c4f7b8"
    old_relation = ProductComponentRelationFactory(
        build_id=image_id, build_type=SoftwareBuild.Type.PYXIS, software_build=None
    )
    # We create a build and root component the first time we process this manifest
    result = slow_fetch_pyxis_manifest(image_id)
    assert result is True
    post.reset_mock()
    post.return_value.json.return_value = wrapped_manifest

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

    # The query's text, structure, or included fields may change and this test will still pass
    # But we can at least assert it's called once with the right image ID and other args
    post.assert_called_once_with(
        settings.PYXIS_GRAPHQL_URL,
        json={"query": Template(query).substitute(manifest_id=image_id, page=0, page_size=50)},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        cert=(settings.PYXIS_CERT, settings.PYXIS_KEY),
        timeout=10,
    )
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
        assert field in query

    assert software_build.source
    sca.assert_called_once_with(str(software_build.uuid), force_process=False)
    taxonomy.assert_called_once_with(image_id, SoftwareBuild.Type.PYXIS)


@pytest.mark.django_db
@patch("corgi.tasks.common.slow_save_taxonomy.delay")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
@patch("corgi.collectors.pyxis.session.post")
def test_slow_fetch_empty_pyxis_manifest(post, sca, taxonomy):
    """Test that we can process a manifest with no repositories"""
    with open("tests/data/pyxis/empty_manifest.json", "r") as data:
        manifest = json.load(data)
    post.return_value.json.return_value = {"data": {"get_content_manifest": {"data": manifest}}}

    image_id = "64dccc5b6d82013739c4f7b8"
    slow_fetch_pyxis_manifest(image_id)
    assert Component.objects.count() == 0
    assert SoftwareBuild.objects.count() == 0

    # The build / Pyxis manifest doesn't have a source URL for this image
    # We don't call the SCA task in this case to avoid many errors in the monitoring email
    assert not manifest["image"].get("source")
    sca.assert_not_called()
    taxonomy.assert_not_called()


@pytest.mark.django_db
@patch("corgi.tasks.pyxis.get_repo_by_nvr")
def test_slow_fetch_pyxis_image_by_nvr(mock_get_repo_name_by_nvr):
    # Test that if the nvr doesn't exist in the Cache, it's looked up from Pyxis
    slow_fetch_pyxis_image_by_nvr("")
    assert mock_get_repo_name_by_nvr.called_with("")

    # Test that if the nvr already exists in the Cache, return it's repo information
    nvr = "test-1-release"
    repo_name = "namespace/name"
    pyxis_image = CollectorPyxisImage.objects.create(nvr=nvr, creation_date=now(), image_id="blah")
    pyxis_repo = CollectorPyxisImageRepository.objects.create(name=repo_name)
    pyxis_image.repos.add(pyxis_repo)

    result = slow_fetch_pyxis_image_by_nvr(nvr)
    assert result == repo_name

    # Test that if slow_fetch_pyxis_image_by_nvr is called with force_process
    # get_repo_by_nvr is called and the result is returned from the cache
    mock_get_repo_name_by_nvr.reset_mock()
    mock_get_repo_name_by_nvr.return_value = "some/container"
    result = slow_fetch_pyxis_image_by_nvr(nvr, force_process=True)
    assert mock_get_repo_name_by_nvr.called_with(nvr)
    assert result == "some/container"


@pytest.mark.django_db
@patch("corgi.tasks.pyxis.slow_fetch_pyxis_image_by_nvr", return_value="some/repo")
def test_slow_update_name_for_container_from_pyxis(mock_fetch):
    nvr = "package-1-release"
    name, version, release = Brew.split_nvr(nvr)
    ContainerImageComponentFactory(
        type=Component.Type.CONTAINER_IMAGE, name=name, version=version, release=release
    )
    result = slow_update_name_for_container_from_pyxis(nvr)
    assert result
    assert Component.objects.filter(name="repo").exists()

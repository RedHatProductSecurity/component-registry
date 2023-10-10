import json
from unittest.mock import patch

import pytest

from corgi.core.models import Component, SoftwareBuild
from corgi.tasks.pyxis import slow_fetch_pyxis_manifest

pytestmark = pytest.mark.unit


@pytest.mark.django_db
@patch("corgi.tasks.brew.slow_save_taxonomy.delay")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
@patch("corgi.collectors.pyxis.session.post")
def test_slow_fetch_pyxis_manifest(post, sca, taxonomy):
    with open("tests/data/pyxis/manifest.json", "r") as data:
        manifest = json.loads(data.read())
    post.return_value.json.return_value = {"data": {"get_content_manifest": {"data": manifest}}}

    image_id = "64dccc5b6d82013739c4f7b8"
    slow_fetch_pyxis_manifest(image_id)

    image_index = Component.objects.get(
        name="image-controller", type=Component.Type.CONTAINER_IMAGE
    )

    software_build = SoftwareBuild.objects.get(
        build_id=image_id, build_type=SoftwareBuild.Type.PYXIS
    )

    components = {}
    for node in image_index.cnodes.get_queryset():
        for child in node.get_children():
            components[child.obj.type] = components.get(child.obj.type, [])
            components[child.obj.type].append(child.purl)

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

    assert software_build.source
    sca.assert_called_once_with(str(software_build.uuid), force_process=False)
    taxonomy.assert_called_once_with(image_id, SoftwareBuild.Type.PYXIS)


@pytest.mark.django_db
@patch("corgi.tasks.brew.slow_save_taxonomy.delay")
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

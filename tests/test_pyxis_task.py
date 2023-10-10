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

    sca.assert_called_once_with(str(software_build.uuid), force_process=False)
    taxonomy.assert_called_once_with(image_id, SoftwareBuild.Type.PYXIS)

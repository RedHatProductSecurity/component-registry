import json
import os

import pytest

from corgi.collectors.pyxis import Pyxis

pytestmark = pytest.mark.unit


def test_get_manifest_data(requests_mock):
    manifest_id = "64dccc646d82013739c4f7e0"
    with open("tests/data/pyxis/manifest.json") as data:
        text_data = data.read()
        example = json.loads(text_data)
        requests_mock.post(os.getenv("CORGI_PYXIS_GRAPHQL_URL"), text=text_data)
    manifest = Pyxis().get_manifest_data(manifest_id)

    assert manifest == example["data"]["get_content_manifest"]["data"]


def test_get_images_by_nvr(requests_mock):
    nvr = (
        "ose-cluster-ovirt-csi-operator-container-v4.12.0-202301042354.p0.gfeb14fb.assembly.stream"
    )
    with open("tests/data/pyxis/images_by_nvr.json") as data:
        text_data = data.read()
        example = json.loads(text_data)
        requests_mock.post(os.getenv("CORGI_PYXIS_GRAPHQL_URL"), text=text_data)
    images = Pyxis().get_image_by_nvr(nvr)
    assert images == example["data"]["find_images_by_nvr"]["data"]

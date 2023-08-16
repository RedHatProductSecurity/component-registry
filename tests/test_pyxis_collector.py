import json
from unittest.mock import patch

import pytest

from corgi.collectors.pyxis import get_manifest_data

pytestmark = pytest.mark.unit


def test_get_manifest_data():
    manifest_id = "64dccc646d82013739c4f7e0"
    with patch("corgi.collectors.pyxis.session.post") as post:
        with open("tests/data/pyxis/manifest.json") as data:
            example = json.load(data)
        post.return_value.json.return_value = {"data": {"get_content_manifest": {"data": example}}}
        manifest = get_manifest_data(manifest_id)

    assert manifest == example

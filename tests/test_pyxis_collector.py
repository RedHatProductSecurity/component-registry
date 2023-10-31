import json
from unittest.mock import patch

import pytest
from django.utils.timezone import now

from config.settings.base import PYXIS_REST_API_URL
from corgi.collectors.models import CollectorPyxisImage, CollectorPyxisImageRepository
from corgi.collectors.pyxis import (
    _extract_repo_and_image_name,
    get_manifest_data,
    get_repo_by_nvr,
    get_repo_for_label,
)

pytestmark = pytest.mark.unit


def test_get_manifest_data():
    manifest_id = "64dccc646d82013739c4f7e0"
    with patch("corgi.collectors.pyxis.session.post") as post:
        with open("tests/data/pyxis/manifest.json") as data:
            example = json.load(data)
        post.return_value.json.return_value = {"data": {"get_content_manifest": {"data": example}}}
        manifest = get_manifest_data(manifest_id)

    assert manifest == example


@pytest.mark.django_db
def test_get_image_by_nvr(requests_mock):
    nvr = (
        "ose-cluster-ovirt-csi-operator-container-v4.12.0-202301042354.p0.gfeb14fb.assembly.stream"
    )
    with open("tests/data/pyxis/images_by_nvr.json") as data:
        requests_mock.get(f"{PYXIS_REST_API_URL}/v1/images/nvr/{nvr}", text=data.read())

    get_repo_by_nvr(nvr)

    assert CollectorPyxisImage.objects.count() == 4
    assert CollectorPyxisImageRepository.objects.count() == 1

    repo = CollectorPyxisImageRepository.objects.get()
    assert repo.name == "openshift4/ovirt-csi-driver-rhel8-operator"
    assert repo.registry == "registry.access.redhat.com"
    assert (
        repo.manifest_list_digest
        == "sha256:2b55f17547ddcbdbefddde7372bc3ddfba9b66dfe76d453e479f9efd51514656"
    )
    assert repo.tags == ["v4.12.0-202301042354.p0.gfeb14fb.assembly.stream"]

    for image in CollectorPyxisImage.objects.all():
        assert image.nvr == nvr
        assert image.name_label == "openshift/ose-ovirt-csi-driver-operator"
        assert repo in image.repos.all()


@pytest.mark.django_db
def test_get_image_with_multiple_repos_by_nvr(requests_mock):
    nvr = "hostpath-csi-driver-container-v4.11.7-5"
    with open("tests/data/pyxis/images_in_multiple_repos_by_nvr.json") as data:
        requests_mock.get(f"{PYXIS_REST_API_URL}/v1/images/nvr/{nvr}", text=data.read())

    with pytest.raises(ValueError):
        get_repo_by_nvr(nvr)


@pytest.mark.django_db
def test_get_repo_and_name_for_label():
    # The Image does not exist yet
    result = get_repo_for_label("label")
    assert not result

    # The 'normal' case of Image with label exists with repo_name set to str with '/'
    pyxis_image = CollectorPyxisImage.objects.create(
        name_label="label", creation_date=now(), image_id="blah"
    )
    pyxis_repo = CollectorPyxisImageRepository.objects.create(name="namespace/name")

    pyxis_image.repos.add(pyxis_repo)
    repo = get_repo_for_label("label")
    assert repo == "namespace/name"

    # The repo is linked to an image but it's name has no '/'
    other_pyxis_repo = CollectorPyxisImageRepository.objects.create(name="name")
    other_pyxis_image = CollectorPyxisImage.objects.create(
        name_label="other_label", creation_date=now(), pyxis_id=1
    )
    other_pyxis_image.repos.add(other_pyxis_repo)
    repo = get_repo_for_label("other_label")
    assert repo == "name"

    # Raise an error if there are multiple repos for an image with the label
    other_pyxis_image.repos.add(pyxis_repo)
    with pytest.raises(ValueError):
        get_repo_for_label("other_label")

    # There are multiple images with the same repo, this is to be expected
    pyxis_image_x = CollectorPyxisImage.objects.create(
        name_label="label", creation_date=now(), pyxis_id=2
    )
    pyxis_image_x.repos.add(pyxis_repo)
    repo = get_repo_for_label("label")
    assert repo == "namespace/name"


def test_extract_repo_and_image_name():
    # Extract from an empty set returns None
    repo_names = set()
    nvr = "name-1-release"
    result = _extract_repo_and_image_name(nvr, repo_names)
    assert not result

    # Extract from repo_names with multiple names raises an error
    repo_names_disparate = {"some/repo", "other/repo"}
    with pytest.raises(ValueError):
        _extract_repo_and_image_name(nvr, repo_names_disparate)

    # The normal case
    repo_names = {"some/repo"}
    result = _extract_repo_and_image_name(nvr, repo_names)
    assert result == "some/repo"

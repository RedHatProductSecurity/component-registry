import os
from pathlib import Path, PosixPath
from unittest.mock import patch

import pytest

from corgi.collectors.syft import Syft
from corgi.core.models import Component, ComponentNode
from corgi.tasks.sca import (
    _archive_source,
    _download_lookaside_sources,
    _get_distgit_sources,
    software_composition_analysis,
)
from tests.factories import ComponentFactory, SoftwareBuildFactory

pytestmark = pytest.mark.unit


def test_parse_components():
    with open("tests/data/crio-syft.json", "r") as crio_test_data:
        results = Syft.parse_components(crio_test_data.read())
    assert len(results) > 0
    names = [r["meta"]["name"] for r in results]
    # When vendor directories are included we get entries like this because of relative replace
    # directives in nested go.mod files
    assert "../" not in names
    assert "github.com/Microsoft/go-winio" in names
    assert results[0]["analysis_meta"] == {"source": "syft", "version": "0.48.1"}


archive_source_test_data = [
    # We've created test/data/rpms/nodejs.git to simulate prod/stage where that dir exists.
    # In that case, the git archive command should change to that directory by invoking with cwd arg
    (
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/nodejs#3cbed2be4171502499d0d89bea1ead91690af7d2",
        "nodejs",
        "rpms",
        "tests/data/rpms/nodejs/3cbed2be4171502499d0d89bea1ead91690af7d2.tar",
        "",
    ),
    (
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/openshift-enterprise-console#f95972ce68d2850ae20c10fbf87182a17fa24b19",
        "openshift-enterprise-console",
        "containers",
        "tests/data/containers/openshift-enterprise-console/"
        "f95972ce68d2850ae20c10fbf87182a17fa24b19.tar",
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/openshift-enterprise-console",
    ),
]


@pytest.mark.parametrize(
    "source_url,package_name,package_type,expected_filename,remote_name",
    archive_source_test_data,
)
@patch("corgi.tasks.sca._call_git_archive")
def test_archive_source(
    mock_git_archive, source_url, package_name, package_type, expected_filename, remote_name
):
    target_file, package_name = _archive_source(source_url, package_type)
    mock_git_archive.assert_called_once()
    if remote_name:
        assert f"--remote={remote_name}" in mock_git_archive.call_args.args[0]
    else:
        assert "cwd" in mock_git_archive.call_args.kwargs
    assert package_name == package_name
    expected_target_file = PosixPath(expected_filename)
    assert target_file == expected_target_file
    assert not expected_target_file.exists()


download_lookaside_test_data = [
    (
        # $BREW_URL/buildinfo?buildID=1210605
        # spec file removed
        "tests/data/rpms/containernetworking-plugins/containernetworking-plugins-v0.8.6-source.tar",
        "containernetworking-plugins",
        "v0.8.6.tar.gz",
        "md5/85eddf3d872418c1c9d990ab8562cc20/",
    ),
    # Nothing gets downloaded because the sources file in the distgit archive is empty
    (
        # This is just an empty archive
        "tests/data/containers/openshift-enterprise-hyperkube/"
        "20f817be5fafe03bdbfff4a3bc561166bfb14013.tar",
        "openshift-enterprise-hyperkube",
        None,
        None,
    ),
]


@patch("subprocess.check_call")
@patch("corgi.tasks.sca._download_lookaside_sources")
def test_get_distgit_sources(mock_git_archive, mock_download_lookaside_sources):
    expected_path = "tests/data/rpms/cri-o/1e52fcdc84be253b5094b942c2fec23d7636d644.tar"
    _ = _get_distgit_sources(
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/cri-o#1e52fcdc84be253b5094b942c2fec23d7636d644",
        "rpms",
    )
    mock_git_archive.assert_called_with(PosixPath(expected_path), "cri-o")


@pytest.mark.parametrize(
    "test_data_file,package_name,expected_filename,expected_path", download_lookaside_test_data
)
def test_download_lookaside_sources(
    test_data_file, package_name, expected_filename, expected_path, requests_mock
):
    distgit_source_archive = Path(test_data_file)
    expected_url = (
        f"https://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/repo/rpms/{package_name}/"
        f"{expected_filename}/{expected_path}{expected_filename}"
    )
    print(f"mocking call to {expected_url}")
    requests_mock.get(expected_url, text="resp")
    downloaded_sources = _download_lookaside_sources(distgit_source_archive, package_name)
    if expected_filename:
        full_expected_filename = (
            f"tests/data/rpms/{package_name}/{expected_filename}/"
            f"{expected_path}/{expected_filename}"
        )
        assert downloaded_sources == [PosixPath(full_expected_filename)]
        for source in downloaded_sources:
            if source != test_data_file:
                os.remove(source)
    else:
        assert downloaded_sources == []


# mock the syft call to avoid having to have actual source code for the test
# Dummy tar files are prefetch to
# tests/data/rpms/cri-o/1e52fcdc84be253b5094b942c2fec23d7636d644.tar (with only sources)
# tests/data/rpms/cri-o/cri-o-41c0779.tar.gz/sha516/<sha256>/cri-o-41c0779.tar.gz (empty file)
@patch("subprocess.check_output")
def test_software_composition_analysis(mock_syft):
    sb = SoftwareBuildFactory(
        build_id=2018747,
        name="cri-o",
        source=f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined below
        "/rpms/cri-o#1e52fcdc84be253b5094b942c2fec23d7636d644",
    )
    srpm_root = ComponentFactory(type=Component.Type.SRPM, software_build=sb, name="cri-o")
    srpm_root.cnodes.get_or_create(type=ComponentNode.ComponentNodeType.SOURCE, parent=None)
    assert not Component.objects.filter(
        type=Component.Type.GOLANG, name="github.com/Microsoft/go-winio"
    ).exists()
    with open("tests/data/crio-syft.json", "r") as crio_test_data:
        mock_syft.return_value = crio_test_data.read()
    software_composition_analysis(2018747)
    assert Component.objects.filter(
        type=Component.Type.GOLANG, name="github.com/Microsoft/go-winio"
    ).exists()
    srpm_root = Component.objects.get(type=Component.Type.SRPM, software_build=sb)
    assert "pkg:golang/github.com/Microsoft/go-winio@v0.5.1" in srpm_root.provides

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
    _scan_remote_sources,
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


@patch("subprocess.check_call")
@patch("corgi.tasks.sca._download_lookaside_sources")
def test_get_distgit_sources(mock_git_archive, mock_download_lookaside_sources):
    expected_path = "tests/data/rpms/cri-o/1e52fcdc84be253b5094b942c2fec23d7636d644.tar"
    _ = _get_distgit_sources(
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/cri-o#1e52fcdc84be253b5094b942c2fec23d7636d644",
        "rpms",
    )
    mock_git_archive.assert_called_with(PosixPath(expected_path), "cri-o", "rpms")


download_lookaside_test_data = [
    (
        # $BREW_URL/buildinfo?buildID=1210605
        # spec file removed
        "tests/data/rpms/containernetworking-plugins/containernetworking-plugins-v0.8.6-source.tar",
        "containernetworking-plugins",
        "rpms",
        "v0.8.6.tar.gz",
        "md5/85eddf3d872418c1c9d990ab8562cc20/",
    ),
    (
        # Nothing gets downloaded because the sources file in the distgit archive is empty
        "tests/data/containers/openshift-enterprise-hyperkube/"  # joined with below
        "20f817be5fafe03bdbfff4a3bc561166bfb14013.tar",
        "openshift-enterprise-hyperkube",
        "containers",
        None,
        None,
    ),
    (
        # buildID=2096033
        # Dummy distgit archive with all but 'sources' file removed
        "tests/data/containers/metrics-schema-installer/"  # joined with below
        "98012f1be90440f90612dc50f2c916e84466d913.tar",
        "metrics-schema-installer",
        "containers",
        "hawkular-metrics-schema-installer-0.31.0.Final-redhat-1.jar",
        "md5/587372e4c72d1eddfab8e848457f574e/",
    ),
]


@pytest.mark.parametrize(
    "test_data_file,package_name,package_type,expected_filename,expected_path",
    download_lookaside_test_data,
)
def test_download_lookaside_sources(
    test_data_file, package_name, package_type, expected_filename, expected_path, requests_mock
):
    distgit_source_archive = Path(test_data_file)
    expected_url = (
        f"https://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/repo/{package_type}/{package_name}/"
        f"{expected_filename}/{expected_path}{expected_filename}"
    )
    print(f"mocking call to {expected_url}")
    requests_mock.get(expected_url, text="resp")
    downloaded_sources = _download_lookaside_sources(
        distgit_source_archive, package_name, package_type
    )
    if expected_filename:
        full_expected_filename = (
            f"tests/data/{package_type}/{package_name}/{expected_filename}/"
            f"{expected_path}/{expected_filename}"
        )
        assert downloaded_sources == [PosixPath(full_expected_filename)]
        for source in downloaded_sources:
            if source != test_data_file:
                os.remove(source)
    else:
        assert downloaded_sources == []


software_composition_analysis_test_data = [
    # Dummy tar files are prefetch to
    # tests/data/rpms/cri-o/1e52fcdc84be253b5094b942c2fec23d7636d644.tar (with only sources)
    # tests/data/rpms/cri-o/cri-o-41c0779.tar.gz/sha516/<sha256>/cri-o-41c0779.tar.gz (empty file)
    (
        2018747,
        "cri-o",
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/rpms/cri-o"  # joined with below
        "#1e52fcdc84be253b5094b942c2fec23d7636d644",
        "tests/data/crio-syft.json",
        "pkg:golang/github.com/Microsoft/go-winio@v0.5.1",
    ),
    # Dummy tar file created in
    # tests/data/containers/grafana/a7b5d3a9cca53e9753102d74adbf630e77337d5c.tar
    (
        1890406,
        "grafana-container",
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/containers/grafana"  # joined with below
        f"#a7b5d3a9cca53e9753102d74adbf630e77337d5c",
        "tests/data/grafana-syft.json",
        "pkg:npm/acorn-globals@4.3.4",
    ),
    (
        1888203,
        "cnf-tests-container",
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}/containers/cnf-tests"  # joined with below
        "#e7efcd0e4fee97567f9eca2ec0d5f0d6b48b5afb",
        "tests/data/cnf-tests-syft.json",
        "pkg:pypi/requests@2.26.0",
    ),
]


@pytest.mark.parametrize(
    "build_id,package_name,dist_git_source,syft_results,expected_purl",
    software_composition_analysis_test_data,
)
# mock the syft call to avoid having to have actual source code for the test
@patch("subprocess.check_output")
def test_software_composition_analysis(
    mock_syft,
    build_id,
    package_name,
    dist_git_source,
    syft_results,
    expected_purl,
):
    sb = SoftwareBuildFactory(
        build_id=build_id,
        name=package_name,
        source=dist_git_source,
    )
    if package_name.endswith("-container"):
        root_component = ComponentFactory(
            type=Component.Type.CONTAINER_IMAGE, software_build=sb, name=package_name, arch="noarch"
        )
    else:
        root_component = ComponentFactory(
            type=Component.Type.SRPM, software_build=sb, name=package_name
        )
    root_component.cnodes.get_or_create(type=ComponentNode.ComponentNodeType.SOURCE, parent=None)
    assert not Component.objects.filter(purl=expected_purl).exists()
    with open(syft_results, "r") as mock_scan_results:
        mock_syft.return_value = mock_scan_results.read()
    software_composition_analysis(build_id)
    assert Component.objects.filter(purl=expected_purl).exists()
    if package_name.endswith("-container"):
        root_component = Component.objects.get(
            type=Component.Type.CONTAINER_IMAGE, arch="noarch", software_build=sb
        )
    else:
        root_component = Component.objects.get(type=Component.Type.SRPM, software_build=sb)
    assert expected_purl in root_component.provides


scan_remote_sources_test_data = [
    (
        # buildID=2026282
        "openshift-enterprise-console-container",
        "remote-source-cachito-gomod-with-deps.tar.gz",
        "tests/data/openshift-enterprise-console-syft.json",
        "pkg:npm/adjust-sourcemap-loader@1.2.0",
    )
]


@pytest.mark.parametrize(
    "package_name,remote_sources_filename,syft_results,expected_package",
    scan_remote_sources_test_data,
)
@patch("subprocess.check_output")
def test_scan_remote_sources(
    mock_syft, package_name, remote_sources_filename, syft_results, expected_package, requests_mock
):
    mock_remote_source_tar_url = f"https://test/{package_name}/{remote_sources_filename}"
    root_component = ComponentFactory(
        type=Component.Type.CONTAINER_IMAGE, name=package_name, arch="noarch"
    )
    root_node, _ = root_component.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=None
    )
    remote_source = ComponentFactory(
        type=Component.Type.UPSTREAM,
        meta_attr={"remote_source_archive": mock_remote_source_tar_url},
    )
    container_source_cnode, _ = remote_source.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE, parent=root_node
    )
    root_component.save_component_taxonomy()
    requests_mock.get(mock_remote_source_tar_url, text="")
    with open(syft_results, "r") as mock_scan_results:
        mock_syft.return_value = mock_scan_results.read()
    _scan_remote_sources(root_component, root_node)
    mock_syft.assert_called_with(
        [
            "/usr/local/bin/syft",
            "packages",
            "-q",
            "-o=syft-json",
            "--exclude=**/vendor/**",
            "file:tests/data/containers/" f"{root_component.nvr}/{remote_sources_filename}",
        ],
        text=True,
    )
    # This is done once after all Syft.scan_files calls in software_component_analysis task
    root_component.save_component_taxonomy()
    assert expected_package in root_component.provides

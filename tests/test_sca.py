import os
import shutil
from pathlib import Path, PosixPath
from typing import Optional, Tuple
from unittest.mock import call, patch

import pytest

from corgi.collectors.syft import Syft
from corgi.core.models import Component, ComponentNode
from corgi.tasks.sca import (
    _clone_source,
    _download_lookaside_sources,
    _get_distgit_sources,
    _scan_remote_sources,
    slow_software_composition_analysis,
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
    (
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/nodejs",
        "3cbed2be4171502499d0d89bea1ead91690af7d2",
        "nodejs",
        "rpms",
        "tests/data/rpms/nodejs",
        True,
    ),
    (
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/openshift-enterprise-console",
        "f95972ce68d2850ae20c10fbf87182a17fa24b19",
        "openshift-enterprise-console",
        "containers",
        "tests/data/containers/openshift-enterprise-console",
        False,
    ),
]


@pytest.mark.parametrize(
    "source_url,commit,package_name,package_type,expected_path,path_exists",
    archive_source_test_data,
)
@patch("subprocess.check_call")
def test_clone_source(
    mock_subprocess, source_url, commit, package_name, package_type, expected_path, path_exists
):
    # This prevents multiple runs of this test from having different results because
    # we mkdir the directory prior to the clone
    if not path_exists:
        shutil.rmtree(expected_path, ignore_errors=True)
        _clone_source(f"{source_url}#{commit}", package_type)
        print(mock_subprocess.call_arg_list)
        mock_subprocess.assert_has_calls(
            [
                call(["/usr/bin/git", "clone", source_url, PosixPath(expected_path)]),
                call(["/usr/bin/git", "checkout", commit], cwd=PosixPath(expected_path)),
            ]
        )
    else:
        with pytest.raises(FileExistsError):
            _clone_source(f"{source_url}#{commit}", package_type)
        mock_subprocess.assert_not_called()


@patch("subprocess.check_call")
@patch("corgi.tasks.sca._download_lookaside_sources")
def test_get_distgit_sources(mock_check_call, mock_download_lookaside_sources):
    expected_path = "tests/data/rpms/test"
    _ = _get_distgit_sources(
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/test#1e52fcdc84be253b5094b942c2fec23d7636d644",
        "rpms",
    )
    print(mock_check_call.call_arg_list)
    mock_check_call.assert_called_with(PosixPath(expected_path), "test", "rpms")
    shutil.rmtree("tests/data/rpms/test")


download_lookaside_test_data = [
    (
        # $BREW_URL/buildinfo?buildID=1210605
        # spec file removed
        "tests/data/rpms/containernetworking-plugins",
        "containernetworking-plugins",
        "rpms",
        "v0.8.6.tar.gz",
        "md5/85eddf3d872418c1c9d990ab8562cc20/",
    ),
    (
        # Nothing gets downloaded because the sources file in the distgit source is empty
        "tests/data/containers/openshift-enterprise-hyperkube",
        "openshift-enterprise-hyperkube",
        "containers",
        None,
        None,
    ),
    (
        # buildID=2096033
        # Dummy distgit archive with all but 'sources' file removed
        "tests/data/containers/metrics-schema-installer",
        "metrics-schema-installer",
        "containers",
        "hawkular-metrics-schema-installer-0.31.0.Final-redhat-1.jar",
        "md5/587372e4c72d1eddfab8e848457f574e/",
    ),
]


@pytest.mark.parametrize(
    "test_sources,package_name,package_type,expected_filename,expected_path",
    download_lookaside_test_data,
)
def test_download_lookaside_sources(
    test_sources, package_name, package_type, expected_filename, expected_path, requests_mock
):
    distgit_source_archive = Path(test_sources)
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
            f"tests/data/lookaside/{package_type}/{package_name}/{expected_filename}/"
        )
        assert downloaded_sources == [PosixPath(full_expected_filename)]
        shutil.rmtree("tests/data/lookaside")
    else:
        assert downloaded_sources == []


slow_software_composition_analysis_test_data = [
    # Dummy tar files are prefetch to
    # tests/data/rpms/cri-o/sources
    # tests/data/lookaside/rpms/cri-o/cri-o-41c0779.tar.gz (empty file)
    (
        2018747,
        "cri-o",
        "cri-o-41c0779.tar.gz",
        "tests/data/crio-syft.json",
        "pkg:golang/github.com/Microsoft/go-winio@v0.5.1",
    ),
    # Dummy tar file created in
    # tests/data/containers/<package-name> (without -container suffix)
    # These repos have no lookaside cache, so they set an empty lookaside_tarfile
    (
        1890406,
        "grafana-container",
        "",
        "tests/data/grafana-syft.json",
        "pkg:npm/acorn-globals@4.3.4",
    ),
    (
        1888203,
        "cnf-tests-container",
        "",
        "tests/data/cnf-tests-syft.json",
        "pkg:pypi/requests@2.26.0",
    ),
]


def mock_clone(package_name: str, package_type: str) -> Tuple[Optional[Path], str]:
    package_name = package_name.removesuffix("-container")
    return Path("tests/data") / package_type / package_name, package_name


@pytest.mark.parametrize(
    "build_id,package_name,lookaside_tarfile,syft_results,expected_purl",
    slow_software_composition_analysis_test_data,
)
# mock the syft call to avoid having to have actual source code for the test
@patch("subprocess.check_output")
@patch("corgi.tasks.sca._clone_source", side_effect=mock_clone)
def test_slow_software_composition_analysis(
    mock_clone_source,
    mock_syft,
    build_id,
    package_name,
    lookaside_tarfile,
    syft_results,
    expected_purl,
):
    sb = SoftwareBuildFactory(
        build_id=build_id,
        name=package_name,
        source=package_name,
    )
    is_container = package_name.endswith("-container")
    if is_container:
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
    slow_software_composition_analysis(build_id)
    package_type = "rpms"
    if is_container:
        package_type = "containers"
    expected_syft_call_arg_list = [
        call(
            [
                "/usr/local/bin/syft",
                "packages",
                "-q",
                "-o=syft-json",
                "--exclude=**/vendor/**",
                f"dir:tests/data/{package_type}/{package_name.removesuffix('-container')}",
            ],
            text=True,
        ),
    ]
    if lookaside_tarfile:
        expected_syft_call_arg_list.append(
            call(
                [
                    "/usr/local/bin/syft",
                    "packages",
                    "-q",
                    "-o=syft-json",
                    "--exclude=**/vendor/**",
                    f"file:tests/data/lookaside/{package_type}/{package_name}/{lookaside_tarfile}",
                ],
                text=True,
            )
        )
    assert mock_syft.call_args_list == expected_syft_call_arg_list
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

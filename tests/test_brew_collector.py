import copy
import json
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.db import IntegrityError
from yaml import safe_load

from corgi.collectors.brew import Brew, BrewBuildTypeNotSupported
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    SoftwareBuild,
)
from corgi.tasks.brew import (
    fetch_unprocessed_relations,
    load_brew_tags,
    load_stream_brew_tags,
    save_component,
    slow_fetch_brew_build,
)
from corgi.tasks.common import BUILD_TYPE
from tests.conftest import setup_product
from tests.data.image_archive_data import (
    KOJI_LIST_RPMS,
    NO_RPMS_IN_SUBCTL_CONTAINER,
    NOARCH_RPM_IDS,
    RPM_BUILD_IDS,
    TEST_IMAGE_ARCHIVE,
)
from tests.factories import (
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
    ProductStreamFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

pytestmark = pytest.mark.unit

# TODO add mock data for commented tests in build_corpus
# build id, source URL, namespace, name, license_declared_raw, type
build_corpus = [
    # firefox -brew buildID=1872838
    # (
    #    1872838,
    #    f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
    #    "/rpms/firefox#1b69e6c1315abe3b4a74f455ea9d6fed3c22bbfe",
    #    "redhat",
    #    "firefox",
    #    "MPLv1.1 or GPLv2+ or LGPLv2+",
    #    "rpm",
    # ),
    # grafana-container: brew buildID=1872940
    (
        1872940,
        "git://pkgs.example.com/containers/grafana#1d4356446cbbbb0b23f08fe93e9deb20fe5114bf",
        "grafana-container",
        "",
        "image",
    ),
    # rh - nodejs: brew buildID=1700251
    # (
    #    1700251,
    #    f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
    #    "/rpms/nodejs#3cbed2be4171502499d0d89bea1ead91690af7d2",
    #    "redhat",
    #    "rh-nodejs12-nodejs",
    #    "MIT and ASL 2.0 and ISC and BSD",
    #    "rpm",
    # ),
    # rubygem: brew buildID=936045
    (
        936045,
        "git://pkgs.example.com/rpms/rubygem-bcrypt#4deddf4d5f521886a5680853ebccd02e3cabac41",
        "rubygem-bcrypt",
        "MIT",
        "rpm",
    ),
    # jboss-webserver-container:
    # brew buildID=1879214
    # (
    #    1879214,
    #    f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
    #    "/containers/jboss-webserver-3#73d776be91c6adc3ae70b795866a81e72e161492",
    #    "redhat",
    #    "jboss-webserver-3-webserver31-tomcat8-openshift-container",
    #    "",
    #    "image",
    # ),
    # org.apache.cxf-cxf: brew buildID=1796072
    # TODO: uncomment when Maven build type is supported
    # (
    #     1796072,
    #     os.getenv("CORGI_TEST_CODE_URL"),
    #     "redhat",
    #     "org.apache.cxf-cxf",
    #     "",
    #     "maven",
    # ),
    # nodejs: brew buildID=1700497
    # (
    #    1700497,
    #    f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
    #    f"/modules/nodejs?#e457a1b700c09c58beca7e979389a31c98cead34",
    #    "redhat",
    #    "nodejs",
    #    "MIT",
    #    "module",
    # ),
    # cryostat-rhel8-operator-container:
    # brew buildID=1841202
    # (
    #    1841202,
    #    f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
    #    "/containers/cryostat-operator#ec07a9a48444e849f9282a8b1c158a93bf667d1d",
    #    "redhat",
    #    "cryostat-rhel8-operator-container",
    #    "",
    #    "image",
    # ),
    # ansible-tower-messaging-container
    # brew buildID=903617
    (
        903617,
        "git://pkgs.example.com/containers/"
        "ansible-tower-messaging#bef542c8527bf77fe9b02d6c2d2c60455fe7e510",
        "ansible-tower-messaging-container",
        "",
        "image",
    ),
]


class MockBrewResult(object):
    pass


@patch("koji.ClientSession")
@patch("corgi.collectors.brew.Brew.brew_rpm_headers_lookup")
@pytest.mark.parametrize(
    "build_id,build_source,build_name,license_declared_raw,build_type", build_corpus
)
def test_get_component_data(
    mock_headers_lookup,
    mock_koji,
    build_id,
    build_source,
    build_name,
    license_declared_raw,
    build_type,
    monkeypatch,
):
    mock_rpm_infos = []
    for function in ("getBuild", "getBuildType", "listTags", "listRPMs"):
        with open(f"tests/data/brew/{build_id}/{function}.yaml") as data:
            pickled_data = safe_load(data.read())
            mock_func = getattr(mock_koji, function)
            mock_func.return_value = pickled_data
            if function == "listRPMs":
                mock_rpm_infos = pickled_data
    brew = Brew(BUILD_TYPE)
    monkeypatch.setattr(brew, "koji_session", mock_koji)

    mock_rpm_info_headers = []
    for rpm_info in mock_rpm_infos:
        with open(f"tests/data/brew/{build_id}/rpms/{rpm_info['id']}/rpmHeaders.yaml") as data:
            pickled_rpm_headers = safe_load(data.read())
        headers_result = MockBrewResult()
        headers_result.result = pickled_rpm_headers
        mock_rpm_info_headers.append((rpm_info, headers_result))

    mock_headers_lookup.return_value = mock_rpm_info_headers

    if build_type not in Brew.SUPPORTED_BUILD_TYPES:
        with pytest.raises(BrewBuildTypeNotSupported):
            brew.get_component_data(build_id)
        return
    c = brew.get_component_data(build_id)
    if build_type == "module":
        assert list(c.keys()) == ["type", "namespace", "meta", "build_meta"]
        assert set(c["meta"].keys()) == {"source"}
    # TODO: uncomment when Maven build type is supported
    # elif build_type == "maven":
    #     assert list(c.keys()) == ["type", "namespace", "meta", "build_meta"]
    elif build_type == "image":
        assert list(c.keys()) == [
            "type",
            "meta",
            "nested_builds",
            "sources",
            "image_components",
            "components",
            "build_meta",
        ]
        assert set(c["meta"].keys()) == {
            "parent",
            "build_parent_nvrs",
            "release",
            "version",
            "arch",
            "epoch",
            "name",
            "digests",
            "source",
            "pull",
        }
    else:
        assert list(c.keys()) == [
            "type",
            "namespace",
            "meta",
            "components",
            "build_meta",
        ]
        assert set(c["meta"].keys()) == {
            "nvr",
            "name",
            "version",
            "release",
            "epoch",
            "arch",
            "source",
            "rpm_id",
            "description",
            "license",
            "source_files",
            "summary",
            "url",
        }
    assert c["build_meta"]["build_info"]["source"] == build_source
    assert c["build_meta"]["build_info"]["build_id"] == build_id
    assert c["build_meta"]["build_info"]["name"] == build_name
    assert c["build_meta"]["build_info"]["type"] == build_type
    # The "license_declared_raw" field on the Component model defaults to ""
    # But the Brew collector (via get_rpm_build_data) / Koji (via getRPMHeaders) may return None
    assert c["meta"].get("license", "") == license_declared_raw


# build_info, list_archives,remote_sources_name
remote_source_in_archive_data = [
    # buildID=1475846
    (
        {
            "name": "quay-clair-container",
            "version": "v3.4.0",
            "release": "25",
            "epoch": None,
            "extra": {
                "image": {},
                "typeinfo": {
                    "remote-sources": {
                        "remote_source_url": "https://example.com/api/v1/requests/28637/download"
                    }
                },
            },
        },
        [
            {"btype": "remote-sources", "filename": "remote-source.tar.gz", "type_name": "tar"},
            {"btype": "remote-sources", "filename": "remote-source.json", "type_name": "json"},
        ],
        ["remote-source-quay-clair-container.json"],
    ),
    # buildID=1911112
    (
        {
            "name": "quay-registry-container",
            "version": "v3.6.4",
            "release": "2",
            "epoch": None,
            "extra": {
                "image": {},
                "typeinfo": {
                    "remote-sources": [
                        {
                            "name": "quay",
                            "url": "https://example.com/api/v1/requests/238481",
                            "archives": ["remote-source-quay.json", "remote-source-quay.tar.gz"],
                        },
                        {
                            "name": "config-tool",
                            "url": "https://example.com/api/v1/requests/238482",
                            "archives": [
                                "remote-source-config-tool.json",
                                "remote-source-config-tool.tar.gz",
                            ],
                        },
                        {
                            "name": "jwtproxy",
                            "url": "https://example.com/api/v1/requests/238483",
                            "archives": [
                                "remote-source-jwtproxy.json",
                                "remote-source-jwtproxy.tar.gz",
                            ],
                        },
                        {
                            "name": "pushgateway",
                            "url": "https://example.com/api/v1/requests/238484",
                            "archives": [
                                "remote-source-pushgateway.json",
                                "remote-source-pushgateway.tar.gz",
                            ],
                        },
                    ],
                },
            },
        },
        [],  # Not used in this case
        ["quay", "config-tool", "jwtproxy", "pushgateway"],
    ),
    # buildID=2011884
    (
        {
            "name": "openshift-enterprise-hyperkube-container",
            "version": "v4.10.0",
            "release": "202205120735.p0.g3afdacb.assembly.stream",
            "epoch": None,
            "extra": {
                "image": {},
                "typeinfo": {
                    # Changing the file names here, just to make a single remote_source test entry
                    # consistent with 1475846
                    "remote-sources": [
                        {
                            "archives": ["remote-source.json", "remote-source.tar.gz"],
                            "name": "cachito-gomod-with-deps",
                            "url": "https://example.com/api/v1/requests/309649",
                        }
                    ]
                },
            },
        },
        [],  # Not used in this case
        ["remote-source-openshift-enterprise-hyperkube-container.json"],
    ),
]


@patch("koji.ClientSession")
@pytest.mark.parametrize(
    "build_info,list_archives,remote_sources_names", remote_source_in_archive_data
)
def test_get_container_build_data_remote_sources_in_archives(
    mock_koji_session, build_info, list_archives, remote_sources_names, monkeypatch, requests_mock
):
    mock_koji_session.listArchives.return_value = list_archives

    download_path = (
        f"packages/{build_info['name']}/{build_info['version']}/"
        f"{build_info['release']}/files/remote-sources"
    )

    print(f"download_path: {download_path}")

    if len(remote_sources_names) == 1:
        with open(f"tests/data/{remote_sources_names[0]}", "r") as remote_source_data:
            requests_mock.get(
                f"{settings.BREW_DOWNLOAD_ROOT_URL}/{download_path}/remote-source.json",
                text=remote_source_data.read(),
            )
    else:
        for path in remote_sources_names:
            with open(f"tests/data/remote-source-{path}.json", "r") as remote_source_data:
                requests_mock.get(
                    f"{settings.BREW_DOWNLOAD_ROOT_URL}/{download_path}/remote-source-"
                    f"{path}.json",
                    text=remote_source_data.read(),
                )

    brew = Brew(BUILD_TYPE)
    monkeypatch.setattr(brew, "koji_session", mock_koji_session)
    c = brew.get_container_build_data(1475846, build_info)
    assert len(c["sources"]) == len(remote_sources_names)
    if len(remote_sources_names) == 1:
        assert (
            c["sources"][0]["meta"]["remote_source_archive"]
            == f"{settings.BREW_DOWNLOAD_ROOT_URL}/"
            f"{download_path}/remote-source.tar.gz"
        )
    else:
        download_urls = [s["meta"]["remote_source_archive"] for s in c["sources"]]
        assert download_urls == [
            f"{settings.BREW_DOWNLOAD_ROOT_URL}/{download_path}/remote-source-{path}.tar.gz"
            for path in remote_sources_names
        ]


def test_extract_remote_sources(requests_mock):
    # buildID=1475846
    json_url = "https://test/data/remote-source-quay-clair-container.json"
    remote_sources = {"28637": (json_url, "tar.gz")}
    with open("tests/data/remote-source-quay-clair-container.json") as remote_source_data:
        requests_mock.get(json_url, text=remote_source_data.read())
    source_components = Brew._extract_remote_sources("", remote_sources)
    assert len(source_components) == 1
    assert source_components[0]["meta"]["name"] == "thomasmckay/clair"
    assert source_components[0]["type"] == Component.Type.GITHUB
    assert len(source_components[0]["components"]) == 375
    xtext_modules = [
        d for d in source_components[0]["components"] if d["meta"]["name"] == "golang.org/x/text"
    ]
    assert len(xtext_modules) == 1
    assert len(xtext_modules[0]["components"]) == 13
    xtext_package_names = [p["meta"]["name"] for p in xtext_modules[0]["components"]]
    assert all([n.startswith("golang.org/x/text") for n in xtext_package_names])
    # verify_pypi_provides test
    pip_modules = [
        d for d in source_components[0]["components"] if d["type"] == Component.Type.PYPI
    ]
    assert len(pip_modules) == 1
    assert pip_modules[0]["meta"]["name"] == "clair"


def test_extract_multiple_remote_sources(requests_mock):
    # buildId=1911112
    remote_sources = {
        "238481": ("https://test/data/remote-source-quay.json", "tar.gz"),
        "238482": ("https://test/data/remote-source-config-tool.json", "tar.gz"),
        "238483": ("https://test/data/remote-source-jwtproxy.json", "tar.gz"),
        "238484": ("https://test/data/remote-source-pushgateway.json", "tar.gz"),
    }
    for remote_source in ["quay", "config-tool", "jwtproxy", "pushgateway"]:
        with open(f"tests/data/remote-source-{remote_source}.json") as remote_source_data:
            requests_mock.get(
                f"https://test/data/remote-source-{remote_source}.json",
                text=remote_source_data.read(),
            )
    go_version = "v1.16.0"
    source_components = Brew._extract_remote_sources(go_version, remote_sources)
    assert len(source_components) == 4
    components = [len(s["components"]) for s in source_components]
    # TODO FAIL: what do these numbers mean?!?
    #  They seem to be related to the number of provided components we discover
    #  I still need to check these numbers are correct after my changes
    assert components == [2, 493, 143, 338]

    # Inspect quay components
    components = [s["components"] for s in source_components]
    quay_npm_components = components[0][0]["components"]
    quay_npm_runtime_components = [c for c in quay_npm_components if not c["meta"]["dev"]]
    assert len(quay_npm_runtime_components) > 0
    quay_npm_dev_components = [c for c in quay_npm_components if c["meta"]["dev"]]
    assert len(quay_npm_dev_components) > 0
    quay_pip_components = components[0][1]["components"]
    quay_pip_runtime_components = [c for c in quay_pip_components if not c["meta"]["dev"]]
    assert len(quay_pip_runtime_components) > 0
    quay_pip_dev_components = [c for c in quay_pip_components if c["meta"]["dev"]]
    assert len(quay_pip_dev_components) == 0

    # Inspect config-tool components
    config_tool_components = components[1]
    config_tool_npm_components = [
        c["components"] for c in config_tool_components if c["meta"]["name"] == "quay-config-editor"
    ]
    assert len(config_tool_npm_components) > 0

    # Inspect jwtproxy components
    jwtproxy_components = components[2]
    # TODO: jwtproxy_components[0] is github.com/quay/jwtproxy/v2 after my changes
    #  AKA the parent component's list of dependencies now includes itself, fix this bug
    assert jwtproxy_components[1]["type"] == Component.Type.GOLANG
    assert jwtproxy_components[1]["meta"]["name"] == "bufio"
    assert jwtproxy_components[1]["meta"]["go_component_type"] == "go-package"
    assert jwtproxy_components[1]["meta"]["version"] == go_version

    # Inspect pushgateway components
    assert len(source_components[3]["components"]) > 0


legacy_osbs_test_data = [
    (
        # buildID=1890187
        {
            "name": "golang-github-prometheus-node_exporter-container",
            "version": "v4.10.0",
            "release": "202202160023.p0.g0eed310.assembly.stream",
            "epoch": None,
            "extra": {
                "image": {
                    "go": {"modules": [{"module": "github.com/openshift/node_exporter"}]},
                },
            },
        },
        ("github.com/openshift/node_exporter",),
    ),
    (
        {
            "name": "cincinnati-operator-metadata-container",
            "version": "v0.0.1",
            "release": "33",
            "epoch": None,
            "extra": {
                "image": {
                    "go": {
                        "modules": [{"module": "https://github.com/cincinnati/cincinnati-operator"}]
                    }
                }
            },
        },
        ("github.com/cincinnati/cincinnati-operator",),
    ),
]


@patch("koji.ClientSession")
@pytest.mark.parametrize("build_info, upstream_go_modules", legacy_osbs_test_data)
def test_get_legacy_osbs_source(mock_koji_session, build_info, upstream_go_modules, monkeypatch):
    mock_koji_session.listArchives.return_value = []
    brew = Brew(BUILD_TYPE)
    monkeypatch.setattr(brew, "koji_session", mock_koji_session)
    result = brew.get_container_build_data(1890187, build_info)
    assert result["meta"]["upstream_go_modules"] == upstream_go_modules


nvr_test_data = [
    (
        "openshift-golang-builder-container-v1.15.5-202012181533.el7",
        "openshift-golang-builder-container",
        "v1.15.5",
        "202012181533.el7",
    ),
    (
        "ubi8-container-8.2-347",
        "ubi8-container",
        "8.2",
        "347",
    ),
]


@pytest.mark.parametrize("nvr,name,version,release", nvr_test_data)
def test_split_nvr(nvr, name, version, release):
    result = Brew.split_nvr(nvr)
    assert result[0] == name
    assert result[1] == version
    assert result[2] == release


def test_parsing_bundled_provides():
    test_provides = [
        # brew rpmID=6809357
        "golang(golang.org/x/crypto/acme)",
        # brew rpmID=10558907
        "golang(aarch-64)",
        # brew rpmID=8261950
        "bundled(golang(github.com/git-lfs/go-netrc)",
        "bundled(golang)(github.com/alexbrainman/sspi)",
        "bundled(golang(golang.org/x/net)",  # Mismatched parens :-|
        # brew rpmID=7025036
        "bundled(python3-certifi)",
        # brew rpmID=9575142
        "bundled(python2-pip)",
        # brew rpmID=9356576
        "bundled(python2dist(setuptools))",
        # brew rpmID=9271398
        "bundled(python3dist(django-stubs))",
        # brew rpmID=10346997
        "bundled(python-selectors2)",
        # brew rpmID=10734880
        "bundled(npm(@babel/code-frame))",
        # brew rpmID=10141992
        "bundled(nodejs-yargs-parser)",
        # brew rpmID=10719029
        "bundled(rubygem-fileutils)",
        # TODO: find artifacts for below
        "bundled(ruby(example))",
        "bundled(rubygem(another-example))",
        # brew rpmID=1689557
        "bundled(crate(aho-corasick/default))",
        # brew rpmID=10141995
        "bundled(rh-nodejs12-zlib)",
        # brew rpmID=10584414
        "bundled(js-backbone)",
        # Unsupported package type
        "bundled(cocoa(hello-world))",
        # Unknown package type; TODO: find example
        "bundled(libsomething)",
        # Below entries do not match bundled deps and are not present in expected values
        "rh-nodejs12-npm",
        "",
        # brew rpmID=8178747
        "bundled(org.yaml:snakeyaml)",
        "bundled(commons-codec:commons-codec)",
        "bundled(biz.source_code:base64coder)",
    ]
    # Add mock version; we're testing component name parsing here only
    test_provides = [(str(provide), "0") for provide in test_provides]

    expected_values = [
        (Component.Type.GOLANG, "golang.org/x/crypto/acme"),
        (Component.Type.GOLANG, "github.com/git-lfs/go-netrc"),
        (Component.Type.GOLANG, "github.com/alexbrainman/sspi"),
        (Component.Type.GOLANG, "golang.org/x/net"),
        (Component.Type.PYPI, "certifi"),
        (Component.Type.PYPI, "pip"),
        (Component.Type.PYPI, "setuptools"),
        (Component.Type.PYPI, "django-stubs"),
        (Component.Type.PYPI, "selectors2"),
        (Component.Type.NPM, "@babel/code-frame"),
        (Component.Type.NPM, "yargs-parser"),
        (Component.Type.GEM, "fileutils"),
        (Component.Type.GEM, "example"),
        (Component.Type.GEM, "another-example"),
        (Component.Type.CARGO, "aho-corasick/default"),
        (Component.Type.GENERIC, "rh-nodejs12-zlib"),
        (Component.Type.NPM, "backbone"),
        (Component.Type.GENERIC, "hello-world"),
        (Component.Type.GENERIC, "libsomething"),
        (Component.Type.MAVEN, "org.yaml/snakeyaml"),
        (Component.Type.MAVEN, "commons-codec/commons-codec"),
        (Component.Type.MAVEN, "biz.source_code/base64coder"),
    ]
    expected_values = [parsed + ("0",) for parsed in expected_values]
    assert Brew._extract_bundled_provides(test_provides) == expected_values


parse_remote_source_url_test_data = [
    (
        "https://github.com/quay/config-tool.git",
        (
            "quay/config-tool",
            Component.Type.GITHUB,
        ),
    ),
    # buildId=2067618
    (
        "git@github.com:rh-gitops-midstream/argo-cd",
        (
            "rh-gitops-midstream/argo-cd",
            Component.Type.GITHUB,
        ),
    ),
    # from OSBS examples:
    (
        "https://git.example.com/team/repo.git",
        ("git.example.com/team/repo", Component.Type.GENERIC),
    ),
    ("git@git.example.com:team/repo.git", ("git.example.com/team/repo", Component.Type.GENERIC)),
]


@pytest.mark.parametrize("url, expected", parse_remote_source_url_test_data)
def test_parse_remote_source_url(url, expected):
    assert Brew._parse_remote_source_url(url) == expected


# test_data_file, expected_component
extract_golang_test_data = [
    (
        "tests/data/remote-source-submariner-operator.json",
        {
            "type": Component.Type.GOLANG,
            "namespace": Component.Namespace.UPSTREAM,
            "meta": {
                "go_component_type": "gomod",
                "name": "github.com/asaskevich/govalidator",
                "version": "v0.0.0-20210307081110-f21760c49a8d",
            },
        },
    ),
    (
        "tests/data/remote-source-poison-pill.json",
        {
            "type": Component.Type.GOLANG,
            "namespace": Component.Namespace.UPSTREAM,
            "meta": {
                "go_component_type": "go-package",
                "name": "bufio",
                "version": "1.15.0",
            },
        },
    ),
]


@pytest.mark.parametrize("test_data_file,expected_component", extract_golang_test_data)
def test_extract_golang(test_data_file, expected_component):
    with open(test_data_file) as testdata:
        testdata = testdata.read()
        testdata = json.loads(testdata, object_hook=lambda d: SimpleNamespace(**d))
    components, remaining = Brew._extract_golang(testdata.dependencies, "1.15.0")
    assert expected_component in components
    assert len(remaining) == 0


@pytest.mark.django_db
def test_save_component():
    software_build = SoftwareBuildFactory()

    # Simulate saving an RPM in a Container
    image_component = ContainerImageComponentFactory(name="image_component")
    root_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=image_component.purl,
        defaults={"obj": image_component},
    )
    rpm_dict = {
        "type": Component.Type.RPM,
        "namespace": Component.Namespace.REDHAT,
        "meta": {"name": "myrpm", "arch": "ppc"},
    }
    save_component(rpm_dict, root_node, software_build)
    # Verify the RPM's software build is None
    assert Component.objects.filter(type=Component.Type.RPM, software_build__isnull=True).exists()

    # Add an SRPM component for that same RPM.
    srpm_component = SrpmComponentFactory(name="srpm_component")
    root_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=srpm_component.purl,
        defaults={"obj": srpm_component},
    )
    rpm_dict = {
        "type": Component.Type.RPM,
        "namespace": Component.Namespace.REDHAT,
        "meta": {"name": "mysrpm", "arch": "src"},
    }
    save_component(rpm_dict, root_node, software_build)
    # Now it should have a software_build
    assert Component.objects.filter(type=Component.Type.RPM, software_build=software_build).exists()


@pytest.mark.django_db
def test_save_component_skips_duplicates():
    """Test that component names which only differ by dash / underscore,
    or different casing, do not create duplicate purls"""
    image_component = ContainerImageComponentFactory(name="image_component")
    root_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=image_component.purl,
        defaults={"obj": image_component},
    )
    name = "REQUESTS_NTLM"
    new_component = {
        "type": Component.Type.PYPI,
        "namespace": Component.Namespace.UPSTREAM,
        "meta": {"name": name, "version": "1.2.3", "arch": "noarch"},
    }

    new_component_with_same_purl = copy.deepcopy(new_component)
    save_component(new_component, root_node, image_component.software_build)
    pypi = Component.objects.get(name=name)
    assert pypi.purl == "pkg:pypi/requests-ntlm@1.2.3"

    name = name.lower().replace("_", "-")
    new_component_with_same_purl["meta"]["name"] = name
    with pytest.raises(IntegrityError):
        # Shouldn't ever happen in real code
        # The duplicate components are created by Syft / the SCA task
        # We can't handle below since we don't know the correct purl yet
        save_component(new_component_with_same_purl, root_node, image_component.software_build)
    # The duplicate should not be saved
    assert Component.objects.filter(name=name).first() is None


@patch("koji.ClientSession")
def test_extract_image_components(mock_koji_session, monkeypatch):
    mock_koji_session.listRPMs.return_value = KOJI_LIST_RPMS
    brew = Brew(BUILD_TYPE)
    monkeypatch.setattr(brew, "koji_session", mock_koji_session)
    noarch_rpms_by_id = {}
    rpm_build_ids = set()
    archive = TEST_IMAGE_ARCHIVE
    noarch_rpms_by_id, result = brew._extract_image_components(
        archive, 1781353, "subctl-container-v0.11.0-51", noarch_rpms_by_id, rpm_build_ids
    )
    assert list(rpm_build_ids) == RPM_BUILD_IDS
    assert result["meta"]["arch"] == "x86_64"
    assert len(result["rpm_components"]) == NO_RPMS_IN_SUBCTL_CONTAINER - len(NOARCH_RPM_IDS)
    for rpm in result["rpm_components"]:
        assert rpm["meta"]["arch"] != "noarch"
    noarch_rpms = noarch_rpms_by_id.values()
    assert len(noarch_rpms) == len(NOARCH_RPM_IDS)
    assert set(noarch_rpms_by_id.keys()) == set(NOARCH_RPM_IDS)


@pytest.mark.django_db
@patch("corgi.tasks.brew.Brew")
@patch("corgi.tasks.sca.cpu_software_composition_analysis.delay")
@patch("corgi.tasks.brew.load_brew_tags")
def test_fetch_rpm_build(mock_load_brew_tags, mock_sca, mock_brew):
    with open("tests/data/brew/1705913/component_data.json", "r") as component_data_file:
        mock_brew.return_value.get_component_data.return_value = json.load(component_data_file)
    slow_fetch_brew_build(1705913)
    srpm = Component.objects.srpms().get(name="cockpit")
    assert srpm.description
    assert srpm.license_declared_raw
    assert srpm.software_build_id
    assert srpm.epoch == 0
    assert srpm.provides.count() == 30
    expected_provides = (
        "pkg:npm/jquery@3.5.1",
        "pkg:generic/xstatic-patternfly-common@3.59.5",
        "pkg:generic/xstatic-bootstrap-datepicker-common@1.9.0",
        "pkg:rpm/redhat/cockpit-system@251-1.el8?arch=noarch",
    )
    assert srpm.provides.filter(purl__in=expected_provides).count() == 4
    assert srpm.upstreams.values_list("purl", flat=True).get() == "pkg:rpm/cockpit@251?arch=noarch"
    # SRPM has no sources of its own (nor is it embedded in any other component)
    assert not srpm.sources.exists()
    cockpit_system = Component.objects.get(
        type=Component.Type.RPM,
        namespace=Component.Namespace.REDHAT,
        name="cockpit-system",
        version="251",
        release="1.el8",
        arch="noarch",
    )
    assert cockpit_system.software_build_id
    # Cockpit has its own SRPM
    assert (
        cockpit_system.sources.values_list("purl", flat=True).get()
        == "pkg:rpm/redhat/cockpit@251-1.el8?arch=src"
    )
    assert sorted(cockpit_system.provides.values_list("purl", flat=True)) == [
        "pkg:generic/xstatic-bootstrap-datepicker-common@1.9.0",
        "pkg:generic/xstatic-patternfly-common@3.59.5",
        "pkg:npm/jquery@3.5.1",
    ]
    jquery = Component.objects.get(
        type=Component.Type.NPM,
        namespace=Component.Namespace.UPSTREAM,
        name="jquery",
        version="3.5.1",
    )
    assert jquery.software_build_id is None
    # jQuery is embedded in Cockpit
    # jQuery has two sources, because both the cockpit SRPM
    # and the cockpit-system container list jQuery in their "provides"
    assert sorted(jquery.sources.values_list("purl", flat=True)) == [
        "pkg:rpm/redhat/cockpit-system@251-1.el8?arch=noarch",
        "pkg:rpm/redhat/cockpit@251-1.el8?arch=src",
    ]

    # See if we checked the tags for brew_tag relations to streams
    mock_load_brew_tags.assert_called_with(
        1705913,
        [
            "rhel-8.5.0-candidate",
            "rhel-8.5.0-Beta-1.0-set",
            "rhel-8.5.0-candidate-Beta-1.0-set",
            "kpatch-kernel-4.18.0-339.el8-build",
        ],
    )


@pytest.mark.django_db
@patch("corgi.tasks.brew.Brew")
@patch("corgi.tasks.brew.cpu_software_composition_analysis.delay")
@patch("corgi.tasks.brew.slow_load_errata.delay")
@patch("corgi.tasks.brew.slow_fetch_brew_build.delay")
def test_fetch_container_build_rpms(mock_fetch_brew_build, mock_load_errata, mock_sca, mock_brew):
    with open("tests/data/brew/1781353/component_data.json", "r") as component_data_file:
        mock_brew.return_value.get_component_data.return_value = json.load(component_data_file)

    stream, _ = setup_product()
    stream.brew_tags = {"rhacm-2.4-rhel-8-container-released": True}
    stream.save()

    slow_fetch_brew_build("1781353", SoftwareBuild.Type.BREW)
    image_index = Component.objects.get(
        name="subctl-container", type=Component.Type.CONTAINER_IMAGE, arch="noarch"
    )

    # Check that new components are related to the build via the brew_tag
    assert ProductComponentRelation.objects.get(
        build_id="1781353", build_type=SoftwareBuild.Type.BREW
    )
    assert image_index.productstreams.filter(pk=stream.uuid).exists()

    softwarebuild = SoftwareBuild.objects.get(build_id=1781353, build_type=SoftwareBuild.Type.BREW)

    noarch_rpms = []
    for node in image_index.cnodes.all():
        for child in node.get_children():
            if child.obj.type == Component.Type.RPM:
                assert child.obj.arch == "noarch"
                noarch_rpms.append(child.obj.purl)
    assert len(noarch_rpms) == len(NOARCH_RPM_IDS)

    child_containers = []
    for node in image_index.cnodes.all():
        for child in node.get_children():
            arch_specific_rpms = []
            if child.obj.type == Component.Type.CONTAINER_IMAGE:
                child_containers.append(child.obj.purl)
                for grandchild in child.get_children():
                    assert grandchild.obj.type == Component.Type.RPM
                    arch_specific_rpms.append(grandchild.obj.purl)
                assert len(arch_specific_rpms) == NO_RPMS_IN_SUBCTL_CONTAINER - len(NOARCH_RPM_IDS)
    assert len(child_containers) == 3

    # Verify calls were made to slow_fetch_brew_build.delay for rpm builds
    assert len(mock_fetch_brew_build.call_args_list) == len(RPM_BUILD_IDS)
    mock_fetch_brew_build.assert_has_calls(
        tuple(
            call(build_id, SoftwareBuild.Type.BREW, save_product=True, force_process=False)
            for build_id in RPM_BUILD_IDS
        ),
        any_order=True,
    )
    mock_load_errata.assert_called_with("RHEA-2021:4610", force_process=False)
    mock_sca.assert_called_with(str(softwarebuild.pk), force_process=False)


@pytest.mark.django_db
@patch("corgi.tasks.brew.Brew")
@patch("corgi.tasks.brew.slow_fetch_modular_build.delay")
def test_load_stream_brew_tags(mock_fetch_modular_build, mock_brew):
    stream = ProductStreamFactory(brew_tags={"rhacm-2.4-rhel-8-container-released": True})
    mock_brew.return_value.get_builds_with_tag.return_value = ["1"]
    load_stream_brew_tags()
    new_brew_tag_relation = ProductComponentRelation.objects.get(
        build_id="1",
        build_type=SoftwareBuild.Type.BREW,
        type=ProductComponentRelation.Type.BREW_TAG,
    )
    assert new_brew_tag_relation.product_ref == stream.name
    mock_fetch_modular_build.assert_called_once()


@pytest.mark.django_db
@patch("corgi.tasks.brew.slow_fetch_brew_build.delay")
@patch("corgi.tasks.brew.slow_fetch_modular_build.delay")
def test_load_brew_tags(mock_fetch_modular_build, mock_fetch_brew_build):
    stream = ProductStreamFactory(brew_tags={"rhacm-2.4-rhel-8-container-released": True})
    load_brew_tags("1", ["rhacm-2.4-rhel-8-container-released"])
    new_brew_tag_relation = ProductComponentRelation.objects.get(
        build_id="1",
        build_type=SoftwareBuild.Type.BREW,
        type=ProductComponentRelation.Type.BREW_TAG,
    )
    assert new_brew_tag_relation.product_ref == stream.name
    mock_fetch_brew_build.assert_not_called()
    mock_fetch_modular_build.assert_not_called()


@pytest.mark.django_db
@patch("corgi.core.models.SoftwareBuild.save_product_taxonomy")
def test_new_software_build_relation(mock_save_prod_tax):
    sb = SoftwareBuildFactory()
    slow_fetch_brew_build(sb.build_id, sb.build_type)
    mock_save_prod_tax.assert_called_once_with()


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
@patch("corgi.tasks.brew.slow_fetch_modular_build.delay")
@patch("corgi.tasks.brew.slow_fetch_brew_build.delay")
def test_load_unprocessed_relations(mock_fetch_brew_task, mock_fetch_modular_task):
    # We don't attempt to fetch non brew builds
    relation = ProductComponentRelationFactory()
    assert not relation.build_id
    assert not fetch_unprocessed_relations()

    # We call the correct task based on the build_type
    ProductComponentRelationFactory(
        build_type=SoftwareBuild.Type.CENTOS,
        build_id=1,
        type=ProductComponentRelation.Type.BREW_TAG,
    )
    no_processed = fetch_unprocessed_relations()
    assert no_processed == 1
    mock_fetch_brew_task.assert_called_once()

    # If the build already exists we don't try to fetch it
    SoftwareBuildFactory(build_type=SoftwareBuild.Type.CENTOS, build_id=1)
    assert not fetch_unprocessed_relations()

    # test fetch by relation_type
    ProductComponentRelationFactory(
        build_type=SoftwareBuild.Type.BREW, build_id=2, type=ProductComponentRelation.Type.COMPOSE
    )
    assert fetch_unprocessed_relations(relation_type=ProductComponentRelation.Type.COMPOSE) == 1
    mock_fetch_modular_task.assert_called_once()


@pytest.mark.django_db(databases=("default", "read_only"), transaction=True)
@patch("corgi.tasks.brew.slow_fetch_modular_build.delay")
def test_load_unprocessed_relations_filters(mock_fetch_modular_task):
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.BREW_TAG,
        build_type=SoftwareBuild.Type.BREW,
        build_id=1,
        product_ref="a",
    )
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.COMPOSE,
        build_type=SoftwareBuild.Type.BREW,
        build_id=2,
        product_ref="b",
    )

    assert fetch_unprocessed_relations(relation_type=ProductComponentRelation.Type.BREW_TAG) == 1
    assert mock_fetch_modular_task.called_with(1)
    assert fetch_unprocessed_relations(product_ref="a") == 1
    assert mock_fetch_modular_task.called_with(2)
    assert (
        fetch_unprocessed_relations(
            product_ref="a", relation_type=ProductComponentRelation.Type.COMPOSE
        )
        == 0
    )


def test_extract_advisory_ids():
    """Test that we discover only errata / advisory names from a list of Brew build tag names"""
    tags = [
        "stream-released",
        "RHBA-2023:1234-released",
        "RHEA-2023:12345-pending",
        "RHSA-2023:123456-dropped",
        "RHSA-2023:1234567-notarealthingyet",
        "RHXA-2023:1234-released",
    ]
    result = Brew.extract_advisory_ids(tags)
    # Only RHBA, RHEA, or RHSA is accepted, not other tags or RHXA
    # Suffixes like -released are stripped
    assert result == [tag.rsplit("-", maxsplit=1)[0] for tag in tags[1:5]]


def test_parse_advisory_ids():
    """Test that we discover only released errata from a list of errata / advisory names"""
    errata_tags = ["RHBA-2023:1234", "RHEA-2023:12345", "RHSA-2023:123456", "RHSA-2023:1234567"]
    result = Brew.parse_advisory_ids(errata_tags)
    # Only 4-digit IDs like 1234 are released
    assert result == errata_tags[:1]


def test_parse_bundled_provides():
    """Test that bundled component data for some SRPM is correct"""
    bundled_golang = (Component.Type.GOLANG, "name1", "version1")
    bundled_npm = (Component.Type.NPM, "name2", "version2")
    bundled_provides = [bundled_golang, bundled_npm]
    rpm_info = {"id": "123"}
    parsed_provides = Brew._parse_bundled_provides(bundled_provides, rpm_info)

    for index, provided in enumerate(parsed_provides):
        assert provided["type"] == bundled_provides[index][0]
        assert provided["namespace"] == Component.Namespace.UPSTREAM
        assert provided["meta"]["name"] == bundled_provides[index][1]
        assert provided["meta"]["version"] == bundled_provides[index][2]
        assert provided["meta"]["rpm_id"] == f"{rpm_info['id']}-bundles-{index + 1}"
        assert provided["meta"]["source"] == ["specfile"]

        # We can't set go_component_type here
        # Both Go modules and Go packages can be bundled into an RPM
        # There's no easy way for us to tell which type this component is
        assert "go_component_type" not in provided["meta"]

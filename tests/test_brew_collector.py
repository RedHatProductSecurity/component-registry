import json
import os
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from corgi.collectors.brew import Brew, BrewBuildTypeNotSupported
from corgi.core.models import Component, ComponentNode
from corgi.tasks.brew import save_component, slow_fetch_brew_build
from tests.data.image_archive_data import (
    NO_RPMS_IN_SUBCTL_CONTAINER,
    NOARCH_RPM_IDS,
    RPM_BUILD_IDS,
    TEST_IMAGE_ARCHIVES,
)
from tests.factories import ComponentFactory, SoftwareBuildFactory

pytestmark = pytest.mark.unit

# build id, namespace, name, license, type
build_corpus = [
    # firefox -brew buildID=1872838
    (
        1872838,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/firefox#1b69e6c1315abe3b4a74f455ea9d6fed3c22bbfe",
        "redhat",
        "firefox",
        "MPLv1.1 or GPLv2+ or LGPLv2+",
        "rpm",
    ),
    # grafana-container: brew buildID=1872940
    (
        1872940,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/grafana#1d4356446cbbbb0b23f08fe93e9deb20fe5114bf",
        "redhat",
        "grafana-container",
        "",
        "image",
    ),
    # rh - nodejs: brew buildID=1700251
    (
        1700251,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/nodejs#3cbed2be4171502499d0d89bea1ead91690af7d2",
        "redhat",
        "rh-nodejs12-nodejs",
        "MIT and ASL 2.0 and ISC and BSD",
        "rpm",
    ),
    # rubygem: brew buildID=936045
    (
        936045,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/rpms/rubygem-bcrypt#4deddf4d5f521886a5680853ebccd02e3cabac41",
        "redhat",
        "rubygem-bcrypt",
        "MIT",
        "rpm",
    ),
    # jboss-webserver-container:
    # brew buildID=1879214
    (
        1879214,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/jboss-webserver-3#73d776be91c6adc3ae70b795866a81e72e161492",
        "redhat",
        "jboss-webserver-3-webserver31-tomcat8-openshift-container",
        "",
        "image",
    ),
    # org.apache.cxf-cxf: brew buildID=1796072
    (
        1796072,
        os.getenv("CORGI_TEST_CODE_URL"),
        "redhat",
        "org.apache.cxf-cxf",
        "",
        "maven",
    ),
    # nodejs: brew buildID=1700497
    (
        1700497,
        os.getenv("CORGI_TEST_CODE_URL"),
        "redhat",
        "it's a module build of nodejs but I didn't bother to fill in the test data",
        "",
        "module",
        # TODO: complete above when support is added
    ),
    # cryostat-rhel8-operator-container:
    # brew buildID=1841202
    (
        1841202,
        f"git://{os.getenv('CORGI_LOOKASIDE_CACHE_URL')}"  # Comma not missing, joined with below
        "/containers/cryostat-operator#ec07a9a48444e849f9282a8b1c158a93bf667d1d",
        "redhat",
        "cryostat-rhel8-operator-container",
        "",
        "image",
    ),
]


@pytest.mark.vcr(match_on=["method", "scheme", "host", "port", "path", "body"])
@pytest.mark.parametrize(
    "build_id,build_source,build_ns,build_name,license,build_type", build_corpus
)
def test_get_component_data(build_id, build_source, build_ns, build_name, license, build_type):
    if build_type not in Brew.SUPPORTED_BUILD_TYPES:
        with pytest.raises(BrewBuildTypeNotSupported):
            Brew().get_component_data(build_id)
        return
    c = Brew().get_component_data(build_id)
    if build_type == "maven":
        assert list(c.keys()) == ["type", "namespace", "meta", "build_meta"]
    elif build_type == "image":
        assert list(c.keys()) == [
            "type",
            "namespace",
            "meta",
            "nested_builds",
            "sources",
            "image_components",
            "components",
            "build_meta",
        ]
    else:
        assert list(c.keys()) == [
            "type",
            "namespace",
            "id",
            "meta",
            "analysis_meta",
            "components",
            "build_meta",
        ]
    assert c["build_meta"]["build_info"]["source"] == build_source
    assert c["build_meta"]["build_info"]["build_id"] == build_id
    assert c["build_meta"]["build_info"]["name"] == build_name
    # The "license" field on the Component model defaults to ""
    # But the Brew collector (via get_rpm_build_data) / Koji (via getRPMHeaders) may return None
    assert c["meta"].get("license", "") == license
    assert c["namespace"] == build_ns
    assert c["type"] == build_type


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
                        "remote_source_url": "https://example.com/api/v1/"
                        "requests/28637/download"
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
                f"{os.getenv('CORGI_BREW_DOWNLOAD_ROOT_URL')}/{download_path}/remote-source.json",
                text=remote_source_data.read(),
            )
    else:
        for path in remote_sources_names:
            with open(f"tests/data/remote-source-{path}.json", "r") as remote_source_data:
                requests_mock.get(
                    f"{os.getenv('CORGI_BREW_DOWNLOAD_ROOT_URL')}/{download_path}/remote-source-"
                    f"{path}.json",
                    text=remote_source_data.read(),
                )

    brew = Brew()
    monkeypatch.setattr(brew, "koji_session", mock_koji_session)
    c = brew.get_container_build_data(1475846, build_info)
    assert len(c["sources"]) == len(remote_sources_names)
    if len(remote_sources_names) == 1:
        assert (
            c["sources"][0]["meta"]["remote_source_archive"]
            == f"{os.getenv('CORGI_BREW_DOWNLOAD_ROOT_URL')}/"
            f"{download_path}/remote-source.tar.gz"
        )
    else:
        download_urls = [s["meta"]["remote_source_archive"] for s in c["sources"]]
        assert download_urls == [
            f"{os.getenv('CORGI_BREW_DOWNLOAD_ROOT_URL')}/{download_path}/"
            f"remote-source-{path}.tar.gz"
            for path in remote_sources_names
        ]


def test_extract_remote_sources(requests_mock):
    # buildID=1475846
    json_url = "http://test/data/remote-source-quay-clair-container.json"
    remote_sources = {"28637": (json_url, "tar.gz")}
    with open("tests/data/remote-source-quay-clair-container.json") as remote_source_data:
        requests_mock.get(json_url, text=remote_source_data.read())
    source_components = Brew()._extract_remote_sources("", remote_sources)
    assert len(source_components) == 1
    assert source_components[0]["meta"]["name"] == "github.com/thomasmckay/clair"
    assert len(source_components[0]["components"]) == 374
    xtext_modules = [
        d for d in source_components[0]["components"] if d["meta"]["name"] == "golang.org/x/text"
    ]
    assert len(xtext_modules) == 1
    assert len(xtext_modules[0]["components"]) == 13
    xtext_package_names = [p["meta"]["name"] for p in xtext_modules[0]["components"]]
    assert all([n.startswith("golang.org/x/text") for n in xtext_package_names])
    # verify_pypi_provides test
    pip_modules = [d for d in source_components[0]["components"] if d["type"] == "pip"]
    assert len(pip_modules) == 1
    assert pip_modules[0]["meta"]["name"] == "clair"


def test_extract_multiple_remote_sources(requests_mock):
    # buildId=1911112
    remote_sources = {
        "238481": ("http://test/data/remote-source-quay.json", "tar.gz"),
        "238482": ("http://test/data/remote-source-config-tool.json", "tar.gz"),
        "238483": ("http://test/data/remote-source-jwtproxy.json", "tar.gz"),
        "238484": ("http://test/data/remote-source-pushgateway.json", "tar.gz"),
    }
    for remote_source in ["quay", "config-tool", "jwtproxy", "pushgateway"]:
        with open(f"tests/data/remote-source-{remote_source}.json") as remote_source_data:
            requests_mock.get(
                f"http://test/data/remote-source-{remote_source}.json",
                text=remote_source_data.read(),
            )
    go_version = "v1.16.0"
    source_components = Brew()._extract_remote_sources(go_version, remote_sources)
    assert len(source_components) == 4
    components = [len(s["components"]) for s in source_components]
    assert components == [2, 492, 142, 337]

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
    assert jwtproxy_components[0]["type"] == "go-package"
    assert jwtproxy_components[0]["meta"]["name"] == "bufio"
    assert jwtproxy_components[0]["meta"]["version"] == go_version

    # Inspect pushgateway components
    assert len(source_components[3]["components"]) > 0


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
    ]
    # Add mock version; we're testing component name parsing here only
    test_provides = [(provide, "0") for provide in test_provides]

    expected_values = [
        ("golang", "golang.org/x/crypto/acme"),
        ("golang", "github.com/git-lfs/go-netrc"),
        ("golang", "github.com/alexbrainman/sspi"),
        ("golang", "golang.org/x/net"),
        ("pypi", "certifi"),
        ("pypi", "pip"),
        ("pypi", "setuptools"),
        ("pypi", "django-stubs"),
        ("pypi", "selectors2"),
        ("npm", "@babel/code-frame"),
        ("npm", "yargs-parser"),
        ("gem", "fileutils"),
        ("gem", "example"),
        ("gem", "another-example"),
        ("crate", "aho-corasick/default"),
        ("unknown", "rh-nodejs12-zlib"),
        ("npm", "backbone"),
        ("unknown:cocoa", "hello-world"),
        ("unknown", "libsomething"),
    ]
    expected_values = [parsed + ("0",) for parsed in expected_values]

    assert Brew._extract_bundled_provides(test_provides) == expected_values


def test_parse_remote_source_url():
    url = "https://github.com/quay/config-tool.git"
    expected = "github.com/quay/config-tool"
    assert Brew._parse_remote_source_url(url) == expected


# test_data_file, expected_component, replace_urls
extract_golang_test_data = [
    (
        "tests/data/remote-source-submariner-operator.json",
        {
            "type": "gomod",
            "meta": {
                "name": "github.com/asaskevich/govalidator",
                "version": "v0.0.0-20210307081110-f21760c49a8d",
            },
        },
        False,
    ),
    (
        "tests/data/remote-source-poison-pill.json",
        {
            "type": "go-package",
            "meta": {
                "name": "bufio",
                "version": "1.15.0",
            },
        },
        True,
    ),
]


@pytest.mark.parametrize("test_data_file,expected_component,replace_urls", extract_golang_test_data)
def test_extract_golang(test_data_file, expected_component, replace_urls):
    brew = Brew()
    with open(test_data_file) as testdata:
        testdata = testdata.read()
        if replace_urls:
            testdata = testdata.replace(
                "{CORGI_TEST_CACHITO_URL}", os.getenv("CORGI_TEST_CACHITO_URL")
            )
        testdata = json.loads(testdata, object_hook=lambda d: SimpleNamespace(**d))
    components, remaining = brew._extract_golang(testdata.dependencies, "1.15.0")
    assert expected_component in components
    assert len(remaining) == 0


def test_save_component():
    software_build = SoftwareBuildFactory()

    # Simulate saving an RPM in a Container
    image_component = ComponentFactory(type=Component.Type.CONTAINER_IMAGE)
    root_node, _ = image_component.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
    )
    rpm_dict = {"type": Component.Type.RPM, "meta": {"name": "myrpm"}}
    save_component(rpm_dict, root_node, software_build)
    saved_rpm = Component.objects.get(type=Component.Type.RPM)
    assert saved_rpm
    # Verify the rpm's software build is None
    assert not saved_rpm.software_build

    # This time save the same RPM in a SRPM
    srpm_component = ComponentFactory(type=Component.Type.SRPM)
    root_node, _ = srpm_component.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
    )
    rpm_dict = {"type": Component.Type.RPM, "meta": {"name": "myrpm"}}
    save_component(rpm_dict, root_node, software_build)
    saved_rpm = Component.objects.get(type=Component.Type.RPM)
    # Now it should have a software_build
    assert saved_rpm.software_build == software_build


@pytest.mark.vcr(match_on=["method", "scheme", "host", "port", "path", "body"])
def test_extract_image_components():
    brew = Brew()
    results = []
    noarch_rpms_by_id = {}
    rpm_build_ids = set()
    for archive in TEST_IMAGE_ARCHIVES:
        noarch_rpms_by_id, result = brew._extract_image_components(
            archive, 1781353, "subctl-container-v0.11.0-51", noarch_rpms_by_id, rpm_build_ids
        )
        results.append(result)
    assert len(results) == len(TEST_IMAGE_ARCHIVES)
    assert list(rpm_build_ids) == RPM_BUILD_IDS
    for image in results:
        assert image["meta"]["arch"] in ["s390x", "x86_64", "ppc64le"]
        assert len(image["rpm_components"]) == NO_RPMS_IN_SUBCTL_CONTAINER - len(NOARCH_RPM_IDS)
        for rpm in image["rpm_components"]:
            assert rpm["meta"]["arch"] != "noarch"
    noarch_rpms = noarch_rpms_by_id.values()
    assert len(noarch_rpms) == len(NOARCH_RPM_IDS)
    assert set(noarch_rpms_by_id.keys()) == set(NOARCH_RPM_IDS)


@pytest.mark.vcr(match_on=["method", "scheme", "host", "port", "path", "body"])
@patch("corgi.tasks.sca.software_composition_analysis.delay")
def test_fetch_rpm_build(mock_sca):
    slow_fetch_brew_build(1705913)
    srpm = Component.objects.get(name="cockpit", type=Component.Type.SRPM)
    assert srpm
    assert srpm.description
    assert srpm.license
    assert srpm.software_build
    assert srpm.software_build.build_id
    assert srpm.epoch == 0
    provides = srpm.get_provides()
    assert len(provides) == 30
    for package in [
        "pkg:npm/jquery@3.5.1",
        "pkg:unknown/xstatic-patternfly-common@3.59.5",
        "pkg:unknown/xstatic-bootstrap-datepicker-common@1.9.0",
        "pkg:rpm/redhat/cockpit-system@251-1.el8?arch=noarch",
    ]:
        assert package in provides
    assert len(srpm.get_upstream()) == 1
    assert len(srpm.get_source()) == 1
    cockpit_system = Component.objects.get(
        type=Component.Type.RPM, name="cockpit-system", version="251", release="1.el8"
    )
    assert cockpit_system.software_build
    jquery = Component.objects.get(type=Component.Type.NPM, name="jquery", version="3.5.1")
    assert not jquery.software_build


@pytest.mark.vcr(match_on=["method", "scheme", "host", "port", "path", "body"])
@patch("config.celery.app.send_task")
@patch("corgi.tasks.brew.slow_fetch_brew_build.delay")
def test_fetch_container_build_rpms(mock_fetch_brew_build, mock_send):
    slow_fetch_brew_build(1781353)
    image_index = Component.objects.get(
        name="subctl-container", type=Component.Type.CONTAINER_IMAGE, arch="noarch"
    )

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
        [call(build_id) for build_id in RPM_BUILD_IDS],
        any_order=True,
    )

    # Verify that load_errata didn't try to fetch the only build in this errata again
    mock_send.assert_not_called

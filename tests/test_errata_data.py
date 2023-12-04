from collections import defaultdict
from copy import deepcopy
from unittest.mock import Mock, call, patch

import pytest
from django.conf import settings

from corgi.collectors.brew import Brew
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
    CollectorErrataRelease,
    CollectorRPMRepository,
)
from corgi.core.constants import MODEL_NODE_LEVEL_MAPPING
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductVariant,
    SoftwareBuild,
)
from corgi.tasks.common import BUILD_TYPE
from corgi.tasks.errata_tool import (
    _get_errata_search_criteria,
    save_errata_relation,
    slow_load_errata,
    slow_load_stream_errata,
    slow_save_errata_product_taxonomy,
    update_variant_repos,
)

from .factories import (
    ProductComponentRelationFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    SoftwareBuildFactory,
)

RELEASE_1659_DATA = {
    "id": 1659,
    "attributes": {
        "name": "3SCALE-3SCALE API MANAGEMENT 2.12.0-RHEL-7",
        "default_brew_tag": "3scale-3scale API Management 2.12.0-rhel-7-candidate",
    },
    "brew_tags": [
        "3scale-3scale API Management 2.12.0-rhel-7-candidate",
        "3scale-3scale API Management 2.12.0-rhel-7-container-candidate",
        "3scale-3scale API Management 2.12.0-rhel-7-candidate",
    ],
    "relationships": {
        "sig_key": {"id": 8, "name": "redhatrelease2"},
        "container_sig_key": {"id": 8, "name": "redhatrelease2"},
    },
}

RELEASE_1660_DATA = {
    "id": 1660,
    "attributes": {
        "name": "3SCALE-3SCALE API MANAGEMENT 2.12.0-RHEL-8",
        "default_brew_tag": "3scale-3scale API Management 2.12.0-rhel-8-candidate",
    },
    "brew_tags": [
        "3scale-3scale API Management 2.12.0-rhel-8-candidate",
        "3scale-3scale API Management 2.12.0-rhel-8-container-candidate",
        "3scale-3scale API Management 2.12.0-rhel-8-candidate",
    ],
    "relationships": {
        "sig_key": {"id": 8, "name": "redhatrelease2"},
        "container_sig_key": {"id": 8, "name": "redhatrelease2"},
    },
}

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def test_update_variant_repos():
    sap_variant = "SAP-8.4.0.Z.EUS"
    sap_repos = [
        "rhel-8-for-ppc64le-sap-netweaver-e4s-debug-rpms__8_DOT_4",
        "rhel-8-for-ppc64le-sap-netweaver-e4s-rpms__8_DOT_4",
    ]
    ha_variants = (
        "HighAvailability-8.2.0.GA",
        "HighAvailability-8.3.0.GA",
    )

    ha_repos = [
        "rhel-8-for-aarch64-highavailability-debug-rpms__8",
        "rhel-8-for-aarch64-highavailability-rpms__8",
    ]
    ps = ProductStreamFactory(name="rhel", version="8.2.0")
    product_node = ProductNode.objects.create(parent=None, obj=ps.products)
    pv_node = ProductNode.objects.create(parent=product_node, obj=ps.productversions)
    ps_node = ProductNode.objects.create(parent=pv_node, obj=ps)

    et_id = 0
    setup_models_for_variant_repos(sap_repos, ps_node, sap_variant, et_id)
    for variant in ha_variants:
        et_id += 1
        setup_models_for_variant_repos(ha_repos, ps_node, variant, et_id)

    update_variant_repos()

    pv = ProductVariant.objects.get(name="SAP-8.4.0.Z.EUS")
    assert pv.pnodes.count() == 1
    pv_node = pv.pnodes.first()
    assert (
        sorted(pv.channels.values_list("name", flat=True))
        == [channel_node.obj.name for channel_node in pv_node.get_descendants()]
        == sap_repos
    )
    # Check that every channel for this Product Variant point to only a single Channel Node since
    # this Variant does not share its repos with any of the HA Variants.
    assert all(channel_node.obj.pnodes.count() == 1 for channel_node in pv_node.get_descendants())

    # HA Variants share the same set of repos, so check that one Channel entity exists that links
    # to two separate pnodes whose immediate parents are the Variants themselves.
    for repo in ha_repos:
        channel = Channel.objects.get(type=Channel.Type.CDN_REPO, name=repo)
        assert channel.pnodes.count() == 2
        assert (
            channel.pnodes.order_by("id")
            .first()
            .get_ancestors()
            .filter(level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"])
            .first()
            .obj.name
            == "HighAvailability-8.2.0.GA"
        )
        assert (
            channel.pnodes.order_by("id")
            .last()
            .get_ancestors()
            .filter(level=MODEL_NODE_LEVEL_MAPPING["ProductVariant"])
            .first()
            .obj.name
            == "HighAvailability-8.3.0.GA"
        )


def setup_models_for_variant_repos(repos, ps_node, variant, et_id):
    et_product = CollectorErrataProduct.objects.create(
        et_id=et_id, name=f"name-{et_id}", short_name=str(et_id)
    )
    et_product_version = CollectorErrataProductVersion.objects.create(
        et_id=et_id, name=f"name-{et_id}", product=et_product
    )
    CollectorErrataProductVariant.objects.create(
        et_id=et_id, name=variant, product_version=et_product_version, repos=repos
    )
    for repo in repos:
        # Some already exist and some don't, so can't do only .get() or .create()
        CollectorRPMRepository.objects.get_or_create(name=repo)

    pv = ProductVariantFactory.create(name=variant)
    ProductNode.objects.create(parent=ps_node, obj=pv)


# id, no_of_obj
errata_details = [
    (
        "77149",
        """    {
          "RHEL-8.4.0.Z.MAIN+EUS": {
            "name": "RHEL-8.4.0.Z.MAIN+EUS",
            "description": "Red Hat Enterprise Linux 8",
            "builds": [
              {
                "ca-certificates-2021.2.50-80.0.el8_4": {
                  "nvr": "ca-certificates-2021.2.50-80.0.el8_4",
                  "nevr": "ca-certificates-0:2021.2.50-80.0.el8_4",
                  "id": 1636922,
                  "is_module": false,
                  "variant_arch": {
                    "BaseOS-8.4.0.Z.MAIN.EUS": {
                      "SRPMS": [
                        "ca-certificates-2021.2.50-80.0.el8_4.src.rpm"
                      ],
                      "noarch": [
                        "ca-certificates-2021.2.50-80.0.el8_4.noarch.rpm"
                      ]
                    }
                  },
                  "added_by": null
                }
              }
            ]
          }
    }""",
        1,
        False,
    ),
    (
        "77150",
        """{
            "RHEL-7-dotNET-3.1": {
              "name": "RHEL-7-dotNET-3.1",
              "description": ".NET Core on Red Hat Enterprise Linux",
              "builds": [
                {
                  "rh-dotnet31-runtime-container-3.1-18": {
                    "nvr": "rh-dotnet31-runtime-container-3.1-18",
                    "nevr": "rh-dotnet31-runtime-container-0:3.1-18",
                    "id": 1628352,
                    "is_module": false,
                    "variant_arch": {
                      "7Server-dotNET-3.1": {
                        "multi": [
                          "docker-image-sha256:20952dbe8bf1159496be299778aaf44a5e50880f10c7689a38c3113c64b70d11.x86_64.tar.gz"
                        ]
                      }
                    },
                    "added_by": null
                  }
                },
                {
                  "rh-dotnet31-container-3.1-18": {
                    "nvr": "rh-dotnet31-container-3.1-18",
                    "nevr": "rh-dotnet31-container-0:3.1-18",
                    "id": 1628358,
                    "is_module": false,
                    "variant_arch": {
                      "7Server-dotNET-3.1": {
                        "multi": [
                          "docker-image-sha256:5f398886270c47b7d8c25f06093202d50cd49866e6f8dee48b6535e9ca144c6f.x86_64.tar.gz"
                        ]
                      }
                    },
                    "added_by": null
                  }
                },
                {
                  "rh-dotnet31-jenkins-agent-container-3.1-27": {
                    "nvr": "rh-dotnet31-jenkins-agent-container-3.1-27",
                    "nevr": "rh-dotnet31-jenkins-agent-container-0:3.1-27",
                    "id": 1628450,
                    "is_module": false,
                    "variant_arch": {
                      "7Server-dotNET-3.1": {
                        "multi": [
                          "docker-image-sha256:883da35d429fae52f0c1b2e7e9e36368d71577baa2334d5e9efc6d1f12d1c898.x86_64.tar.gz"
                        ]
                      }
                    },
                    "added_by": null
                  }
                }
              ]
            }
        }""",
        3,
        True,
    ),
]


@patch("corgi.tasks.brew.slow_update_name_for_container_from_pyxis.delay")
@patch("config.celery.app.send_task")
@pytest.mark.parametrize("erratum_id, build_list, no_of_objs, is_container", errata_details)
def test_save_product_component_for_errata(
    mock_send, mock_update_name, erratum_id, build_list, no_of_objs, is_container, requests_mock
):
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}",
            text=remote_source_data.read(),
        )
    build_list_url = f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json"
    requests_mock.get(build_list_url, text=build_list)
    sb = SoftwareBuildFactory(build_id="1636922", build_type=BUILD_TYPE)
    container_build = SoftwareBuildFactory(build_id="1628358", build_type=SoftwareBuild.Type.BREW)
    container_build.meta_attr["nvr"] = "rh-dotnet31-container-3.1-18"
    container_build.save()
    slow_load_errata(erratum_id)
    pcrs = ProductComponentRelation.objects.filter(external_system_id=erratum_id)
    assert len(pcrs) == no_of_objs
    assert mock_send.call_count == no_of_objs
    for pcr in pcrs:
        # If the relation uses this build's ID
        if pcr.build_id == sb.build_id:
            # assert it is linked to the build using the ForeignKey field
            assert pcr.software_build_id == sb.pk
        elif pcr.build_id == container_build.build_id:
            assert pcr.software_build_id == container_build.pk
        else:
            # else assert the ForeignKey is unset / other build IDs have not been fetched
            assert pcr.software_build_id is None
    if is_container:
        assert mock_update_name.called_with("rh-dotnet31-container-3.1-18")


def test_update_product_component_relation():
    sb = SoftwareBuildFactory()
    ProductComponentRelation.objects.create(
        external_system_id=1, product_ref="variant", build_id=sb.build_id, build_type=sb.build_type
    )
    variant_to_component_map = defaultdict(list)
    variant_to_component_map["variant"].append({sb.build_id: []})
    save_errata_relation(set(), sb.build_type, 1, variant_to_component_map)
    assert ProductComponentRelation.objects.all().count() == 1


@patch("corgi.tasks.errata_tool.app")
def test_slow_save_errata_product_taxonomy(mock_app):
    sb = SoftwareBuildFactory()
    sb2 = SoftwareBuildFactory()
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.ERRATA,
        external_system_id="1",
        software_build=sb,
        build_id=sb.build_id,
        build_type=sb.build_type,
    )
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.ERRATA,
        external_system_id="1",
        software_build=sb2,
        build_id=sb2.build_id,
        build_type=sb2.build_type,
    )
    slow_save_errata_product_taxonomy(1)
    send_task_calls = [
        call("corgi.tasks.common.slow_save_taxonomy", args=(sb.build_id, sb.build_type)),
        call("corgi.tasks.common.slow_save_taxonomy", args=(sb2.build_id, sb2.build_type)),
    ]
    # Calls happen based on build ID / UUID ordering, which is random
    mock_app.send_task.assert_has_calls(send_task_calls, any_order=True)


@patch("corgi.collectors.brew.Brew.persist_modules")
def test_parse_modular_builds(mock_persist_modules):
    # Modified modular build from Errata api/v1/erratum/92462/builds_list.json
    modular_build = {
        "8Base-CertSys-10.4": {
            "noarch": ["redhat-pki-base-10.13.0-2.module+el8pki+14894+cc476c07.noarch.rpm"],
            "SRPMS": ["redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.src.rpm"],
            "x86_64": ["redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64.rpm"],
        }
    }
    build_id = "1979262"
    et = ErrataTool()
    results = defaultdict(list)
    mock_persist_modules.return_value = [build_id]
    et._parse_module(
        "redhat-pki-10-8060020220420152504.07fb4edf", modular_build, Brew("BREW"), results
    )
    assert mock_persist_modules.called_with(
        {
            "redhat-pki-10-8060020220420152504.07fb4edf": [
                "redhat-pki-base-10.13.0-2.module+el8pki+14894+cc476c07.noarch.rpm",
                "redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64.rpm",
            ]
        }
    )
    expected = defaultdict(list)
    expected["8Base-CertSys-10.4"].append(
        {
            build_id: [
                "redhat-pki-base-10.13.0-2.module+el8pki+14894+cc476c07.noarch",
                "redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64",
            ]
        }
    )
    assert results == expected


@patch("corgi.collectors.brew.Brew.persist_modules")
def test_parse_module_errata_components(mock_persist_modules, requests_mock):
    erratum_id = 92462
    build_id = "1979262"
    mock_persist_modules.return_value = [build_id]
    with open("tests/data/errata/errata_modular_builds_list.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_erratum_components(erratum_id)
    assert "8Base-CertSys-10.4" in results.keys()
    assert build_id in results["8Base-CertSys-10.4"][0]


@patch("corgi.collectors.brew.Brew.persist_modules")
def test_parse_module_and_normal_errata_components(mock_persist_modules, requests_mock):
    erratum_id = "92462"
    build_id = 1979262
    mock_persist_modules.return_value = [build_id]
    with open(
        "tests/data/errata/errata_modular_and_normal_builds_list.json", "r"
    ) as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_erratum_components(erratum_id)
    assert "8Base-CertSys-10.4" in results.keys()
    expected_certsys_104_results = {
        build_id: [
            "idm-console-framework-1.3.0-1.module+el8pki+14677+1ef79a68.noarch",
            "ldapjdk-4.23.0-1.module+el8pki+14677+1ef79a68.noarch",
            "python3-redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.noarch",
            "tomcatjss-7.7.2-1.module+el8pki+14677+1ef79a68.noarch",
            "jss-4.9.2-1.module+el8pki+14677+1ef79a68.x86_64",
            "redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64",
        ]
    }
    assert [expected_certsys_104_results] == results["8Base-CertSys-10.4"]
    assert "7Server-7.6.AUS" in results.keys()
    assert [
        {1848203: ["polkit-0.112-18.el7_6.3.src.rpm", "polkit-0.112-18.el7_6.3.x86_64.rpm"]}
    ] == results["7Server-7.6.AUS"]


@patch("corgi.collectors.brew.Brew.persist_modules")
def test_parse_module_and_normal_errata_components_same_variant(
    mock_persist_modules, requests_mock
):
    erratum_id = "92462"
    build_id = 1979262
    mock_persist_modules.return_value = [build_id]
    with open(
        "tests/data/errata/errata_modular_and_normal_builds_list_same_variant.json", "r"
    ) as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_erratum_components(erratum_id)
    assert "8Base-CertSys-10.4" in results.keys()
    expected_certsys_104_results = {
        build_id: [
            "idm-console-framework-1.3.0-1.module+el8pki+14677+1ef79a68.noarch",
            "ldapjdk-4.23.0-1.module+el8pki+14677+1ef79a68.noarch",
            "python3-redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.noarch",
            "tomcatjss-7.7.2-1.module+el8pki+14677+1ef79a68.noarch",
            "jss-4.9.2-1.module+el8pki+14677+1ef79a68.x86_64",
            "redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64",
        ]
    }
    assert expected_certsys_104_results in results["8Base-CertSys-10.4"]
    assert {
        1848203: ["polkit-0.112-18.el7_6.3.src.rpm", "polkit-0.112-18.el7_6.3.x86_64.rpm"]
    } in results["8Base-CertSys-10.4"]


@patch("corgi.collectors.brew.Brew.persist_modules")
def test_parse_module_and_normal_errata_components_mixed_variants(
    mock_persist_modules, requests_mock
):
    erratum_id = "92462"
    build_id = 1979262
    mock_persist_modules.return_value = [build_id]
    with open(
        "tests/data/errata/errata_modular_and_normal_builds_list_mixed_variants.json", "r"
    ) as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_erratum_components(erratum_id)
    assert mock_persist_modules.call_count == 2
    assert "8Base-CertSys-10.4" in results.keys()
    expected_certsys_104_results = {
        build_id: [
            "idm-console-framework-1.3.0-1.module+el8pki+14677+1ef79a68.noarch",
            "ldapjdk-4.23.0-1.module+el8pki+14677+1ef79a68.noarch",
            "tomcatjss-7.7.2-1.module+el8pki+14677+1ef79a68.noarch",
            "jss-4.9.2-1.module+el8pki+14677+1ef79a68.x86_64",
        ]
    }
    assert expected_certsys_104_results in results["8Base-CertSys-10.4"]
    assert "7Server-7.6.AUS" in results.keys()
    assert {
        1848203: ["polkit-0.112-18.el7_6.3.src.rpm", "polkit-0.112-18.el7_6.3.x86_64.rpm"]
    } in results["7Server-7.6.AUS"]
    expected_7server_76_aus_results = {
        build_id: [
            "python3-redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.noarch",
            "redhat-pki-10.13.0-2.module+el8pki+14894+cc476c07.x86_64",
        ]
    }
    assert expected_7server_76_aus_results in results["7Server-7.6.AUS"]


errata_key_details = [
    (
        "RHBA-2023:5017-2",
        120142,
        True,
        True,
    ),
    (
        "RHBA-2023:120271",
        120271,
        False,
        True,
    ),
    (
        "RHBA-2021:3573-02",
        77149,
        True,
        False,
    ),
]


@pytest.mark.parametrize(
    "advisory_name, erratum_id, shipped_live, is_container", errata_key_details
)
def test_shipped_live_advisory(
    advisory_name, erratum_id, shipped_live, is_container, requests_mock
):
    # Test we can translate between advisory_name and id
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{advisory_name}",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_errata_key_details(advisory_name)
    assert results == (erratum_id, shipped_live, is_container)


@pytest.mark.parametrize(
    "advisory_name, erratum_id, shipped_live, is_container", errata_key_details
)
def test_shipped_live_id(advisory_name, erratum_id, shipped_live, is_container, requests_mock):
    # Test we can get details by id
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_errata_key_details(erratum_id)
    assert results == (erratum_id, shipped_live, is_container)


def test_invalid_errtaum_details(requests_mock):
    # This test data contains 2 errata of type rhsa, and rhba. Only one type is expected.
    with open("tests/data/errata/invalid.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/123456",
            text=remote_source_data.read(),
        )
    with pytest.raises(ValueError):
        ErrataTool().get_errata_key_details("123456")


def test_strip_brew_tag_candidate_suffixes():
    brew_tags = ["some-tag", "other-tag-candidate"]
    expected = ["some-tag", "other-tag"]
    result: list[str] = ErrataTool.strip_brew_tag_candidate_suffixes(brew_tags)
    assert expected == result


def test_get_products_and_version(monkeypatch):
    def product_data(path):
        return [
            {
                "id": 176,
                "type": "products",
                "attributes": {"name": "3scale"},
                "relationships": {"product_versions": [{"id": 1659}, {"id": 1660}]},
            }
        ]

    def product_version_data(path):
        if "1659" in path:
            return {"data": RELEASE_1659_DATA}
        elif "1660" in path:
            return {"data": RELEASE_1660_DATA}

    et = ErrataTool()
    monkeypatch.setattr(et, "get_paged", product_data)
    monkeypatch.setattr(et, "get", product_version_data)
    results = et.get_products_and_versions()
    assert "product_versions" in results[0]
    assert "product_versions" not in results[0]["relationships"].keys()
    assert results[0]["product_versions"][0]["id"] == 1659
    assert results[0]["product_versions"][1]["id"] == 1660


def test_load_products_and_versions(monkeypatch):
    products_and_versions = [
        {
            "id": 176,
            "type": "products",
            "attributes": {"name": "3scale", "short_name": "3scale"},
            "relationships": {},
            "product_versions": [
                RELEASE_1659_DATA,
                RELEASE_1660_DATA,
            ],
        }
    ]

    et = ErrataTool()
    et.load_products_and_versions(products_and_versions)
    product = CollectorErrataProduct.objects.first()
    assert product
    assert product.et_id == 176
    assert product.name == "3scale"
    assert CollectorErrataProductVersion.objects.count() == 2
    first_version = CollectorErrataProductVersion.objects.first()
    assert first_version.product == product
    assert first_version.name == "3SCALE-3SCALE API MANAGEMENT 2.12.0-RHEL-7"
    expected_brew_tags = [
        "3scale-3scale API Management 2.12.0-rhel-7",
        "3scale-3scale API Management " "2.12.0-rhel-7-container",
        "3scale-3scale API Management 2.12.0-rhel-7",
    ]
    assert first_version.brew_tags == expected_brew_tags
    assert "attributes", "relationships" in first_version.meta_attr


def test_load_releases():
    product = CollectorErrataProduct.objects.create(et_id=125, name="3scale")
    product_version = CollectorErrataProductVersion.objects.create(
        et_id=632, name="RHEL-7-3scale-AMP-2.0", product=product
    )
    releases = [
        {
            "id": 680,
            "type": "releases",
            "attributes": {
                "name": "3scale API Management 2.0",
                "is_active": True,
                "enabled": True,
                "enable_batching": False,
                "is_async": True,
                "is_silent": False,
            },
            "relationships": {
                "brew_tags": [{"id": 1037, "name": "3scale-amp-2.0-rhel-7-candidate"}],
                "product": {"id": 125, "short_name": "3scale API Management"},
                "product_versions": [{"id": 632, "name": "RHEL-7-3scale-AMP-2.0"}],
                "state_machine_rule_set": {"id": 1, "name": "Default"},
            },
        }
    ]
    et = ErrataTool()
    et.load_releases(releases)
    release_20 = CollectorErrataRelease.objects.get(name="3scale API Management 2.0")
    assert release_20.et_id == 680
    assert release_20.enabled
    assert release_20.brew_tags == ["3scale-amp-2.0-rhel-7"]
    assert product_version in release_20.product_versions.get_queryset()

    # Missing product versions this time
    releases = [
        {
            "id": 1567,
            "type": "releases",
            "attributes": {
                "name": "3scale API Management 2.12",
                "is_active": True,
                "enabled": True,
                "is_deferred": False,
                "zstream_target_release": None,
                "notify_bugzilla_about_release_status": False,
                "is_silent": False,
            },
            "relationships": {
                "brew_tags": [],
                "product": {"id": 125, "short_name": "3scale API Management"},
                "product_versions": [
                    {"id": 1678, "name": "3SCALE-2.12-RHEL-8"},
                    {"id": 1679, "name": "3SCALE-2.12-RHEL-7"},
                ],
                "state_machine_rule_set": None,
            },
        }
    ]
    et = ErrataTool()
    et.load_releases(releases)
    release_212 = CollectorErrataRelease.objects.get(name="3scale API Management 2.12")
    assert not release_212.product_versions.count()


def test_load_variants():
    variants = [
        {
            "id": 2594,
            "type": "variants",
            "attributes": {
                "name": "11Server-11.4.SLES-SAT-TOOLS-6.5",
                "description": "Satellite Tools 6.5 (v. 11sp4 SLES Server)",
                "cpe": "cpe:/a:redhat:sles_satellite_tools:6.5::sles11",
                "override_ftp_base_folder": None,
                "enabled": True,
                "buildroot": False,
                "tps_stream": "None",
                "relationships": {
                    "product": {
                        "id": 107,
                        "name": "Red Hat Satellite Tools",
                        "short_name": "SAT-TOOLS",
                    },
                    "product_version": {"id": 1060, "name": "SAT-TOOLS-6.5-SLES-11.4"},
                    "rhel_release": {"id": 98, "name": "SLES-11"},
                    "rhel_variant": {"id": 2594, "name": "11Server-11.4.SLES-SAT-TOOLS-6.5"},
                    "push_targets": [{"id": 7, "name": "cdn_stage"}, {"id": 4, "name": "cdn"}],
                },
            },
        }
    ]
    et = ErrataTool()
    copy_of_variants = deepcopy(variants)
    et.load_variants(variants)
    variant = CollectorErrataProductVariant.objects.first()
    assert not variant.product_version

    # This time create the linked product variant
    product = CollectorErrataProduct.objects.create(et_id=107, name="Red Hat Satellite Tools")
    product_version = CollectorErrataProductVersion.objects.create(
        et_id=1060, name="SAT-TOOLS-6.5-SLES-11.4", product=product
    )
    copy_of_variants[0]["id"] = 2595
    copy_of_variants[0]["attributes"]["name"] = "Some other variant"
    et.load_variants(copy_of_variants)
    variant = CollectorErrataProductVariant.objects.get(et_id=2595)
    assert variant.name == "Some other variant"
    assert product_version == variant.product_version


def test_parse_container_errata_components(requests_mock):
    erratum_id = 97738
    build_id = 2177946
    with open("tests/data/errata/errata_container_builds.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_erratum_components(erratum_id)
    assert requests_mock.call_count == 1
    assert "8Base-RHACM-2.4" in results.keys()
    assert build_id in results["8Base-RHACM-2.4"][0]


def test_get_errata_search_criteria():
    stream = ProductStreamFactory()
    base7_variant = ProductVariantFactory(name="7Server-RH7-RHOSE-4.6", productstreams=stream)
    base8_variant = ProductVariantFactory(name="8Base-RHOSE-4.6", productstreams=stream)
    result = _get_errata_search_criteria(stream.name)
    stream_variants = [base7_variant.name, base8_variant.name]
    assert result[0] == stream_variants
    assert result[1] == []

    # When variants_from_brew_tags is populated no releases are returned
    stream_releases = [1, 2]
    stream.meta_attr["releases_from_brew_tags"] = stream_releases
    stream.save()
    result = _get_errata_search_criteria(stream.name)
    assert result[0] == stream_variants
    assert result[1] == []

    # When variants are empty return releases
    for variant in stream.productvariants.get_queryset():
        variant.delete()
    assert not stream.productvariants.get_queryset()
    result = _get_errata_search_criteria(stream.name)
    assert result[0] == []
    assert result[1] == stream_releases


def test_do_errata_search(requests_mock):
    et = ErrataTool()
    # Test that an empty search_value raises an error
    with pytest.raises(ValueError):
        et._do_errata_search("", [])

    # Test that a search for a product returns some errata_ids
    search_values = f"{et.SHIPPED_LIVE_SEARCH}&product[]=79"
    paged_search_url = f"/api/v1/erratum/search?{search_values}&page[number]="
    with open("tests/data/errata/search_results.json", "r") as errata_search_results:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}{paged_search_url}1",
            text=errata_search_results.read(),
        )
    # ErrataTool.get_paged looks for an empty data field to signal the end of paged results has been
    # reached
    requests_mock.get(f"{settings.ERRATA_TOOL_URL}{paged_search_url}2", text='{"data":[]}')
    result = et._do_errata_search(search_values, [])
    # These values are from tests/data/errata/search_results.json
    assert 121542 in result
    assert 122119 in result


def test_do_errata_search_filtering_variants(requests_mock):
    # Test that a search for a product with variants filters errata to only the ones with those
    # variants
    et = ErrataTool()
    search_values = f"{et.SHIPPED_LIVE_SEARCH}&product[]=79"
    ose_412_erratum_id = 121542
    ose_411_erratum_id = 122119
    paged_search_url = f"/api/v1/erratum/search?{search_values}&page[number]="
    with open("tests/data/errata/search_results.json", "r") as errata_search_results:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}{paged_search_url}1",
            text=errata_search_results.read(),
        )
    # ErrataTool.get_paged looks for an empty data field to signal the end of paged results has been
    # reached
    requests_mock.get(f"{settings.ERRATA_TOOL_URL}{paged_search_url}2", text='{"data":[]}')
    # Add requests mock endpoint for:
    # api / v1 / erratum / 121542 / builds
    ose_412_variant = "8Base-RHOSE-4.12"
    build_data = (
        """{
        "OSE-4.12-RHEL-8": {
            "name": "OSE-4.12-RHEL-8",
            "description": "Red Hat OpenShift Container Platform 4.12",
            "builds": [
                {
                    "windows-machine-config-operator-bundle-container-v7.1.1-8.1696813827": {
                        "variant_arch": {"%s": []}
                    }
                }
            ]
        }
    }
    """
        % ose_412_variant
    )
    requests_mock.get(
        f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{ose_412_erratum_id}/builds",
        text=build_data,
    )
    build_data = """{
        "OSE-4.11-RHEL-8": {
            "name": "OSE-4.11-RHEL-8",
            "description": "Red Hat OpenShift Container Platform 4.11",
            "builds": [
                {"kernel-4.18.0-372.76.1.el8_6": {"variant_arch": {"8Base-RHOSE-4.11": []}}}
            ]
        }
    }"""
    requests_mock.get(
        f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{ose_411_erratum_id}/builds_list.json",
        text=build_data,
    )
    result = et._do_errata_search(search_values, [(ose_412_variant)])
    assert ose_412_erratum_id in result
    assert ose_411_erratum_id not in result


def test_get_errata_matching_variants(monkeypatch):
    # Test that missing variants raises an Error
    et = ErrataTool()
    with pytest.raises(ValueError):
        et.get_errata_matching_variants(["missing_variant"])

    # Test that do_errata_search is called with the correct product and variant names
    product = CollectorErrataProduct.objects.create(name="product", et_id=1)
    version = CollectorErrataProductVersion.objects.create(
        name="version", et_id=10, product=product
    )
    variant = CollectorErrataProductVariant.objects.create(
        name="variant", et_id=100, product_version=version
    )
    mock_do_errata_search = Mock(return_value=[1])
    monkeypatch.setattr(et, "_do_errata_search", mock_do_errata_search)
    et.get_errata_matching_variants([variant.name])
    assert mock_do_errata_search.call_args == call(
        f"{et.SHIPPED_LIVE_SEARCH}&product[]={product.et_id}", [variant.name], False
    )


def test_get_errata_matching_disparate_variants():
    # Test that calling et_errata_matching_variants with variants from 2 different products raises
    # an error
    product = CollectorErrataProduct.objects.create(name="product", short_name="p", et_id=1)
    version = CollectorErrataProductVersion.objects.create(
        name="version", et_id=10, product=product
    )
    variant = CollectorErrataProductVariant.objects.create(
        name="variant", et_id=100, product_version=version
    )

    other_product = CollectorErrataProduct.objects.create(name="other_product", et_id=2)
    other_version = CollectorErrataProductVersion.objects.create(
        name="other_version", et_id=20, product=other_product
    )
    other_variant = CollectorErrataProductVariant.objects.create(
        name="other_variant", et_id=200, product_version=other_version
    )

    et = ErrataTool()
    with pytest.raises(ValueError):
        et.get_errata_matching_variants([variant.name, other_variant.name])


def test_get_errata_for_release(monkeypatch):
    mock_do_errata_search = Mock(return_value=[1])
    et = ErrataTool()
    monkeypatch.setattr(et, "_do_errata_search", mock_do_errata_search)
    et.get_errata_for_releases([1])
    et.get_errata_for_releases([1], True)
    assert mock_do_errata_search.call_args_list == [
        call("show_state_SHIPPED_LIVE=1&release[]=1", [], False),
        call("show_state_SHIPPED_LIVE=1&release[]=1", [], True),
    ]


@patch("corgi.tasks.errata_tool._get_errata_search_criteria")
@patch("corgi.tasks.errata_tool.slow_load_errata.apply_async")
@patch("corgi.tasks.errata_tool.ErrataTool")
def test_slow_load_errata_stream(mock_et_collector, mock_load_errata, mock_search_criteria):
    # This needs to return the correct number of arguments for the test to compile, the values are
    # used in the 2nd test
    # below to trigger a call to et.get_errata_matching_variants
    mock_search_criteria.return_value = (
        ["some_variant"],
        [],
    )

    # Test that a stream with no variants or releases doesn't load any errata
    stream = ProductStreamFactory()
    result = slow_load_stream_errata(stream.name)
    assert result == 0
    mock_search_criteria.assert_called_once_with(stream.name)
    mock_load_errata.assert_not_called()

    mock_load_errata.reset_mock()
    mock_search_criteria.reset_mock()
    mock_search_criteria.return_value = (
        ["some_variant"],
        [],
    )

    # Test that a stream with variants calls load for errata matching those variants
    mock_et_collector.return_value.get_errata_matching_variants.return_value = [1]
    result = slow_load_stream_errata(stream.name)
    assert result == 1
    mock_load_errata.assert_called_once_with(args=(1, True), priority=0)
    mock_search_criteria.assert_called_once_with(stream.name)

    mock_load_errata.reset_mock()
    mock_search_criteria.reset_mock()
    mock_search_criteria.return_value = (
        [],
        [100],
    )
    mock_et_collector.return_value.get_errata_for_releases.return_value = {100}
    result = slow_load_stream_errata(stream.name, force_process=False)
    assert result == 1
    mock_load_errata.assert_called_once_with(args=(100, False), priority=0)
    mock_search_criteria.assert_called_once_with(stream.name)

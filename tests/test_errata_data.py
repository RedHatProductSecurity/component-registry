from collections import defaultdict
from unittest.mock import call, patch

import pytest
from django.conf import settings

from corgi.collectors.brew import Brew
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
    CollectorRPMRepository,
)
from corgi.core.constants import MODEL_NODE_LEVEL_MAPPING
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductVariant,
)
from corgi.tasks.common import BUILD_TYPE
from corgi.tasks.errata_tool import (
    save_errata_relation,
    slow_load_errata,
    slow_save_errata_product_taxonomy,
    update_variant_repos,
)

from .factories import (
    ProductComponentRelationFactory,
    ProductStreamFactory,
    ProductVariantFactory,
    SoftwareBuildFactory,
)

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
    ),
]


@patch("config.celery.app.send_task")
@pytest.mark.parametrize("erratum_id, build_list, no_of_objs", errata_details)
def test_save_product_component_for_errata(
    mock_send, erratum_id, build_list, no_of_objs, requests_mock
):
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}",
            text=remote_source_data.read(),
        )
    build_list_url = f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json"
    requests_mock.get(build_list_url, text=build_list)
    sb = SoftwareBuildFactory(build_id="1636922", build_type=BUILD_TYPE)
    slow_load_errata(erratum_id)
    pcrs = ProductComponentRelation.objects.filter(external_system_id=erratum_id)
    assert len(pcrs) == no_of_objs
    assert mock_send.call_count == no_of_objs
    for pcr in pcrs:
        # If the relation uses this build's ID
        if pcr.build_id == sb.build_id:
            # assert it is linked to the build using the ForeignKey field
            assert pcr.software_build_id == sb.pk
        else:
            # else assert the ForeignKey is unset / other build IDs have not been fetched
            assert pcr.software_build_id is None


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
        call("corgi.tasks.brew.slow_save_taxonomy", args=(sb.build_id, sb.build_type)),
        call("corgi.tasks.brew.slow_save_taxonomy", args=(sb2.build_id, sb2.build_type)),
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
    ),
    (
        "RHBA-2023:120271",
        120271,
        False,
    ),
]


@pytest.mark.parametrize("advisory_name, erratum_id, shipped_live", errata_key_details)
def test_shipped_live_advisory(advisory_name, erratum_id, shipped_live, requests_mock):
    # Test we can translate between advisory_name and id
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{advisory_name}",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_errata_key_details(advisory_name)
    assert results == (erratum_id, shipped_live)


@pytest.mark.parametrize("advisory_name, erratum_id, shipped_live", errata_key_details)
def test_shipped_live_id(advisory_name, erratum_id, shipped_live, requests_mock):
    # Test we can get details by id
    with open(f"tests/data/errata/{erratum_id}.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}",
            text=remote_source_data.read(),
        )
    results = ErrataTool().get_errata_key_details(erratum_id)
    assert results == (erratum_id, shipped_live)


def test_invalid_errtaum_details(requests_mock):
    # This test data contains 2 errata of type rhsa, and rhba. Only one type is expected.
    with open("tests/data/errata/invalid.json", "r") as remote_source_data:
        requests_mock.get(
            f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/123456",
            text=remote_source_data.read(),
        )
    with pytest.raises(ValueError):
        ErrataTool().get_errata_key_details("123456")

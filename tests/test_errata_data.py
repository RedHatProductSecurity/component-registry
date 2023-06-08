from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
    CollectorRPMRepository,
)
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductVariant,
)
from corgi.tasks.errata_tool import (
    associate_variant_with_build_stream,
    slow_load_errata,
    slow_save_errata_product_taxonomy,
    update_variant_repos,
)

from .factories import (
    ComponentFactory,
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
    ps = ProductStreamFactory.create(name="rhel", version="8.2.0")
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
            .filter(content_type=ContentType.objects.get_for_model(ProductVariant))
            .first()
            .obj.name
            == "HighAvailability-8.2.0.GA"
        )
        assert (
            channel.pnodes.order_by("id")
            .last()
            .get_ancestors()
            .filter(content_type=ContentType.objects.get_for_model(ProductVariant))
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
        name=variant, repos=repos, et_id=et_id, product_version=et_product_version
    )
    for repo in repos:
        CollectorRPMRepository.objects.get_or_create(name=repo)

    pv = ProductVariantFactory.create(name=variant)
    ProductNode.objects.create(object_id=pv.pk, obj=pv, parent=ps_node)


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


@patch("corgi.tasks.errata_tool.associate_variant_with_build_stream")
@patch("config.celery.app.send_task")
@pytest.mark.parametrize("erratum_id, build_list, no_of_objs", errata_details)
def test_save_product_component_for_errata(
    mock_send, mock_associate, erratum_id, build_list, no_of_objs, requests_mock
):
    build_list_url = f"{settings.ERRATA_TOOL_URL}/api/v1/erratum/{erratum_id}/builds_list.json"
    requests_mock.get(build_list_url, text=build_list)
    slow_load_errata(erratum_id)
    pcr = ProductComponentRelation.objects.filter(external_system_id=erratum_id)
    assert len(pcr) == no_of_objs
    assert mock_send.call_count == no_of_objs
    assert mock_associate.call_count == 0


@patch("corgi.tasks.errata_tool.associate_variant_with_build_stream")
@patch("corgi.tasks.errata_tool.slow_save_errata_product_taxonomy.delay")
def test_variants_created_for_stream(mock_save_errata, mock_associate):
    # We have a BREW_TAG relation for build_id 2524805, not 2524641
    ProductComponentRelation.objects.get_or_create(
        external_system_id="rhaos-4.13-rhel-8-container-released",
        product_ref="openshift-4.13.z",
        build_id="2524805",
        build_type="BREW",
        defaults={"type": ProductComponentRelation.Type.BREW_TAG},
    )
    # Simulate calling slow_load_errata with all the builds already fetched
    SoftwareBuildFactory(build_id="2524805", build_type="BREW")
    SoftwareBuildFactory(build_id="2524641", build_type="BREW")
    # These are the ERRATA relations for the builds
    erratum_id = "1"
    ProductComponentRelation.objects.get_or_create(
        external_system_id=erratum_id,
        product_ref="8Base-RHOSE-4.13",
        build_id="2524805",
        build_type="BREW",
        defaults={"type": ProductComponentRelation.Type.ERRATA},
    )
    ProductComponentRelation.objects.get_or_create(
        external_system_id=erratum_id,
        product_ref="8Base-RHOSE-4.13",
        build_id="2524641",
        build_type="BREW",
        defaults={"type": ProductComponentRelation.Type.ERRATA},
    )
    slow_load_errata(erratum_id)
    build_variants = {"2524641": ["8Base-RHOSE-4.13"], "2524805": ["8Base-RHOSE-4.13"]}
    assert mock_save_errata.call_args_list == [call(build_variants, "BREW")]
    slow_save_errata_product_taxonomy(build_variants, "BREW")
    assert mock_associate.call_args_list == [call("2524805", "BREW", ["8Base-RHOSE-4.13"])]


def test_associate_variant_with_streams():
    sb = SoftwareBuildFactory()
    product, _ = CollectorErrataProduct.objects.get_or_create(name="RHOSE", et_id=79)
    product_version, _ = CollectorErrataProductVersion.objects.get_or_create(
        name="OSE-4.13-RHEL-8", et_id=1892, product=product
    )
    CollectorErrataProductVariant.objects.get_or_create(
        name="8Base-RHOSE-4.13",
        cpe="cpe:/a:redhat:openshift:4.13::el8",
        et_id=4160,
        product_version=product_version,
    )
    assert not associate_variant_with_build_stream(sb.build_id, sb.build_type, ["8Base-RHOSE-4.13"])
    assert not ProductVariant.objects.filter(name="8Base-RHOSE-4.13").exists()

    # This time create a linked component with stream to associate
    stream = ProductStreamFactory(active=True)
    component = ComponentFactory(software_build=sb)
    component.productstreams.set([stream])
    assert associate_variant_with_build_stream(sb.build_id, sb.build_type, ["8Base-RHOSE-4.13"])
    product_variant = ProductVariant.objects.get(name="8Base-RHOSE-4.13")
    assert product_variant
    assert product_variant.cpe == "cpe:/a:redhat:openshift:4.13::el8"
    assert product_variant.productstreams == stream


# Assert that we only associate a variant with a stream where a single stream is associated with the
# build
def test_associate_variant_with_many_homogenous_streams():
    sb = SoftwareBuildFactory(build_id="1808180")
    product, _ = CollectorErrataProduct.objects.get_or_create(name="RHOSE", et_id=79)
    product_version, _ = CollectorErrataProductVersion.objects.get_or_create(
        name="OSE-4.9-RHEL-8", et_id=1509, product=product
    )
    CollectorErrataProductVariant.objects.get_or_create(
        name="8Base-RHOSE-4.9",
        cpe="cpe:/a:redhat:openshift:4.9::el8",
        et_id=3481,
        product_version=product_version,
    )
    inactive_stream = ProductStreamFactory(name="openshift-4.9", active=False)
    active_stream = ProductStreamFactory(name="openshift-4.9.z", active=True)
    ose_pod = ComponentFactory(software_build=sb)
    ose_pod.productstreams.set([inactive_stream, active_stream])
    assert not associate_variant_with_build_stream(sb.build_id, sb.build_type, ["8Base-RHOSE-4.9"])
    assert not ProductVariant.objects.filter(name="8Base-RHOSE-4.9").exists()


def test_associate_variant_with_many_disparate_streams():
    # Where there are multiple streams for a component, don't associate any of them
    # with the variant. This keeps the current (correct) rule enforcing a single
    # ProductStream per ProductVariant.
    # This is a contrived example because rhel-8.6.0.z and rhel-8.4.0.z use errata_info,
    # however I couldn't find any component associated with multiple active BREW_TAG streams.
    sb = SoftwareBuildFactory(build_id="1269433")
    product, _ = CollectorErrataProduct.objects.get_or_create(name="RHEL", et_id=16)
    product_version, _ = CollectorErrataProductVersion.objects.get_or_create(
        name="RHEL-8.6.0.Z.EUS", et_id=1663, product=product
    )
    CollectorErrataProductVariant.objects.get_or_create(
        name="BaseOS-8.6.0.Z.EUS",
        cpe="cpe:/o:redhat:rhel_eus:8.6::baseos",
        et_id=3729,
        product_version=product_version,
    )
    rhel_86z = ProductStreamFactory(active=True)
    rhel_84z = ProductStreamFactory(active=True)
    curl = ComponentFactory(software_build=sb)
    curl.productstreams.set([rhel_86z, rhel_84z])
    assert not associate_variant_with_build_stream(
        sb.build_id, sb.build_type, ["BaseOS-8.6.0.Z.EUS"]
    )
    assert not ProductVariant.objects.filter(name="BaseOS-8.6.0.Z.EUS").exists()


# eg. https://errata.devel.redhat.com/advisory/41819/builds
# This is a contrived example because rhel-7.9.z uses errata_info
# However this is the only active stream I could find using multiple
# variants for a single build in an erratum
def test_associate_build_with_multiple_variants():
    sb = SoftwareBuildFactory(build_id="891182")
    product, _ = CollectorErrataProduct.objects.get_or_create(name="RHEL", et_id=16)
    product_version, _ = CollectorErrataProductVersion.objects.get_or_create(
        name="RHEL-7.9.z", et_id=1315, product=product
    )
    CollectorErrataProductVariant.objects.get_or_create(
        name="7Workstation-7.9.Z",
        cpe="cpe:/o:redhat:enterprise_linux:7::workstation",
        et_id=3149,
        product_version=product_version,
    )
    CollectorErrataProductVariant.objects.get_or_create(
        name="7Server-7.9.Z",
        cpe="cpe:/o:redhat:enterprise_linux:7::server",
        et_id=2024,
        product_version=product_version,
    )
    stream = ProductStreamFactory()
    component = ComponentFactory(software_build=sb)
    component.productstreams.set([stream])
    assert associate_variant_with_build_stream(
        sb.build_id, sb.build_type, ["7Workstation-7.9.Z", "7Server-7.9.Z"]
    )
    server_product_variant = ProductVariant.objects.get(name="7Server-7.9.Z")
    workstation_product_variant = ProductVariant.objects.get(name="7Workstation-7.9.Z")
    for variant in (server_product_variant, workstation_product_variant):
        assert variant
        assert variant.productstreams == stream
    assert workstation_product_variant.cpe == "cpe:/o:redhat:enterprise_linux:7::workstation"
    assert server_product_variant.cpe == "cpe:/o:redhat:enterprise_linux:7::server"

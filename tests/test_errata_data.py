from unittest.mock import patch

import pytest

from corgi.collectors.errata_tool import ErrataTool
from corgi.core.models import (
    Channel,
    ProductComponentRelation,
    ProductNode,
    ProductVariant,
)
from corgi.tasks.errata_tool import load_errata, update_variant_repos

from .factories import ProductVariantFactory

pytestmark = pytest.mark.unit


@pytest.mark.vcr
def test_update_variant_repos():
    variants_to_create = (
        "SAP-8.4.0.Z.EUS",
        "HighAvailability-8.2.0.GA",
        "HighAvailability-8.3.0.GA",
    )
    for variant in variants_to_create:
        pv = ProductVariantFactory.create(name=variant)
        ProductNode.objects.create(obj=pv, parent=None)

    update_variant_repos()

    pv = ProductVariant.objects.get(name="SAP-8.4.0.Z.EUS")
    assert pv.pnodes.count() == 1
    pv_node = pv.pnodes.first()
    assert (
        pv.channels
        == [channel_node.obj.name for channel_node in pv_node.get_descendants()]
        == [
            "rhel-8-for-ppc64le-sap-netweaver-e4s-debug-rpms__8_DOT_4",
            "rhel-8-for-ppc64le-sap-netweaver-e4s-rpms__8_DOT_4",
            "rhel-8-for-ppc64le-sap-netweaver-e4s-source-rpms__8_DOT_4",
            "rhel-8-for-ppc64le-sap-netweaver-eus-debug-rpms__8_DOT_4",
            "rhel-8-for-ppc64le-sap-netweaver-eus-rpms__8_DOT_4",
            "rhel-8-for-ppc64le-sap-netweaver-eus-source-rpms__8_DOT_4",
            "rhel-8-for-s390x-sap-netweaver-eus-debug-rpms__8_DOT_4",
            "rhel-8-for-s390x-sap-netweaver-eus-rpms__8_DOT_4",
            "rhel-8-for-s390x-sap-netweaver-eus-source-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-e4s-debug-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-e4s-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-e4s-source-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-eus-debug-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-eus-rpms__8_DOT_4",
            "rhel-8-for-x86_64-sap-netweaver-eus-source-rpms__8_DOT_4",
        ]
    )
    # Check that every channel for this Product Variant point to only a single Channel Node since
    # this Variant does not share its repos with any of the HA Variants.
    assert all(channel_node.obj.pnodes.count() == 1 for channel_node in pv_node.get_descendants())

    # HA Variants share the same set of repos, so check that one Channel entity exists that links
    # to two separate pnodes whose immediate parents are the Variants themselves.
    for repo in [
        "rhel-8-for-aarch64-highavailability-debug-rpms__8",
        "rhel-8-for-aarch64-highavailability-rpms__8",
        "rhel-8-for-aarch64-highavailability-source-rpms__8",
        "rhel-8-for-ppc64le-highavailability-debug-rpms__8",
        "rhel-8-for-ppc64le-highavailability-rpms__8",
        "rhel-8-for-ppc64le-highavailability-source-rpms__8",
        "rhel-8-for-s390x-highavailability-debug-rpms__8",
        "rhel-8-for-s390x-highavailability-rpms__8",
        "rhel-8-for-s390x-highavailability-source-rpms__8",
        "rhel-8-for-x86_64-highavailability-debug-rpms__8",
        "rhel-8-for-x86_64-highavailability-rpms__8",
        "rhel-8-for-x86_64-highavailability-source-rpms__8",
    ]:
        channel = Channel.objects.get(type=Channel.Type.CDN_REPO, name=repo)
        assert channel.pnodes.count() == 2
        assert (
            channel.pnodes.first().get_ancestors().first().obj.name == "HighAvailability-8.2.0.GA"
        )
        assert channel.pnodes.last().get_ancestors().first().obj.name == "HighAvailability-8.3.0.GA"


# id, no_of_obj
errata_details = [("77149", 1), ("RHBA-2021:2382", 3), ("test", 0)]


@pytest.mark.vcr
@patch("config.celery.app.send_task")
@pytest.mark.parametrize("errata_name, no_of_objs", errata_details)
def test_save_product_component_for_errata(mock_send, errata_name, no_of_objs):
    load_errata([errata_name])
    errata_id = ErrataTool().normalize_erratum_id(errata_name)
    pcr = ProductComponentRelation.objects.filter(external_system_id=errata_id)
    assert len(pcr) == no_of_objs

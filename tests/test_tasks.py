import json
from unittest.mock import Mock, call, patch

import koji
import pytest

from corgi.collectors.brew import ADVISORY_REGEX, Brew
from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import (
    Component,
    ComponentNode,
    ComponentTag,
    ProductComponentRelation,
    SoftwareBuild,
    SoftwareBuildTag,
)
from corgi.tasks.brew import (
    slow_delete_brew_build,
    slow_refresh_brew_build_tags,
    slow_update_brew_tags,
)
from corgi.tasks.errata_tool import slow_handle_shipped_errata
from corgi.tasks.pnc import slow_fetch_pnc_sbom

from .factories import (
    ComponentFactory,
    ContainerImageComponentFactory,
    ProductComponentRelationFactory,
    SoftwareBuildFactory,
    SrpmComponentFactory,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.django_db(databases=("default",)),
]

# Raw tags with some data quality issues - we assert these are cleaned up
MISC_TAGS = ["stream-pending", "stream-candidate", "stream-pending", "RHXA-2023:1234-invalid"]
ERRATA_TAGS = [
    "RHBA-2023:1234567-pending",
    "RHEA-2023:12345-dropped",
    "RHBA-2023:1234567-alsopending",
]
RELEASED_TAGS = [
    "RHSA-2023:12345-pending",
    "RHEA-2023:1234-suffixignored",
    "RHEA-2023:1234-suffixignored",
]

EXISTING_TAGS = MISC_TAGS + ERRATA_TAGS + RELEASED_TAGS
CLEAN_TAGS = sorted(set(EXISTING_TAGS))
CLEAN_ERRATA_TAGS = sorted(set(tag.rsplit("-", maxsplit=1)[0] for tag in EXISTING_TAGS[4:]))
CLEAN_RELEASED_TAGS = sorted(set(tag.rsplit("-", maxsplit=1)[0] for tag in EXISTING_TAGS[8:]))

# TODO: Needs much more
# tag_added, is_errata, is_released
test_tag_data = (
    ("RHSA-2023:4321-released", True, True),
    ("RHSA-2023:43210-dropped", True, False),
    ("not_an_errata", False, False),
)


@pytest.mark.parametrize("tag_added,is_errata,is_released", test_tag_data)
def test_slow_update_brew_tags_added(tag_added, is_errata, is_released):
    """Test that builds have their tags and relations updated"""
    build = SoftwareBuildFactory(
        build_type=SoftwareBuild.Type.BREW, meta_attr={"tags": EXISTING_TAGS}
    )

    with patch("corgi.tasks.brew.slow_load_errata.delay") as mock_load_errata:
        slow_update_brew_tags(build.build_id, tag_added=tag_added)
    # Get updated build / tag data from DB after task saves it
    build = SoftwareBuild.objects.get(build_id=build.build_id, build_type=SoftwareBuild.Type.BREW)

    # Below are only modified if tag meets certain conditions
    clean_errata_tags = CLEAN_ERRATA_TAGS
    clean_released_tags = CLEAN_RELEASED_TAGS

    if is_errata:
        tag_without_suffix = tag_added.rsplit("-", maxsplit=1)[0]
        assert ADVISORY_REGEX.match(tag_added).group() == tag_without_suffix
        mock_load_errata.assert_called_once_with(tag_without_suffix)
        clean_errata_tags = sorted(set(clean_errata_tags + [tag_without_suffix]))

        if is_released:
            tag_id = tag_without_suffix.rsplit(":", maxsplit=1)[-1]
            assert len(tag_id) == 4 and tag_id.isdigit()
            clean_released_tags = sorted(set(clean_released_tags + [tag_without_suffix]))
    else:
        mock_load_errata.assert_not_called()

    # All tags end up in tags field
    # Only tags matching ADVISORY_REGEX end up in errata_tags field (with -suffix stripped)
    # Only released advisories (4-digit IDs) end up in released_errata_tags field
    # All fields are automatically sorted and deduped
    assert build.meta_attr["tags"] == sorted(set(CLEAN_TAGS + [tag_added]))
    assert build.meta_attr["errata_tags"] == clean_errata_tags
    assert build.meta_attr["released_errata_tags"] == clean_released_tags


def test_slow_update_brew_tags_removed():
    """Test that builds have their tags updated, but not relations"""
    tag_removed = RELEASED_TAGS[-1]
    tag_removed_without_suffix = tag_removed.rsplit("-", maxsplit=1)[0]
    build = SoftwareBuildFactory(build_type=SoftwareBuild.Type.BREW, meta_attr={"tags": CLEAN_TAGS})

    with patch("corgi.tasks.brew.slow_load_errata.delay") as mock_load_errata:
        slow_update_brew_tags(build.build_id, tag_removed=tag_removed)
    mock_load_errata.assert_not_called()

    # Get updated build / tag data from DB after task saves it
    build = SoftwareBuild.objects.get(build_id=build.build_id, build_type=SoftwareBuild.Type.BREW)

    clean_tags = sorted(set(tag for tag in CLEAN_TAGS if tag != tag_removed))
    clean_errata_tags = sorted(
        set(tag for tag in CLEAN_ERRATA_TAGS if tag != tag_removed_without_suffix)
    )
    clean_released_tags = sorted(
        set(tag for tag in CLEAN_RELEASED_TAGS if tag != tag_removed_without_suffix)
    )

    # All tags end up in tags field
    # Only tags matching ADVISORY_REGEX end up in errata_tags field (with -suffix stripped)
    # Only released advisories (4-digit IDs) end up in released_errata_tags field
    # All fields are automatically sorted and deduped
    assert build.meta_attr["tags"] == clean_tags
    assert build.meta_attr["errata_tags"] == clean_errata_tags
    assert build.meta_attr["released_errata_tags"] == clean_released_tags


def test_slow_update_brew_tags_errors():
    """Test that slow_update_brew_tags handles missing builds and missing tags"""
    build_id = "123"
    warning = slow_update_brew_tags(build_id, tag_added=build_id)
    assert warning == f"Brew build with matching ID not ingested (yet?): {build_id}"

    # meta_attr field for all builds always has tags key set to a list (on ingestion)
    # no need to test missing tags key or values other than lists
    SoftwareBuildFactory(
        build_type=SoftwareBuild.Type.BREW, build_id=build_id, meta_attr={"tags": []}
    )
    with pytest.raises(ValueError):
        # Must supply either tag_added or tag_removed kwarg
        slow_update_brew_tags(build_id)

    with patch("corgi.tasks.brew.slow_refresh_brew_build_tags") as mock_refresh:
        # Refresh all tags if tag to remove isn't found
        # This happens when tags are renamed, e.g. X to Y, then removed
        # We get a UMB event to remove Y, but no event for the rename
        # So our list of tags still has only X, and Y is missing
        warning = slow_update_brew_tags(build_id, tag_removed=build_id)
        assert warning == f"Tag to remove {build_id} not found, so refreshing all tags"
        mock_refresh.delay.assert_called_once_with(int(build_id))


@patch("corgi.tasks.errata_tool.app")
@patch("corgi.tasks.errata_tool.slow_load_errata")
@patch("corgi.tasks.errata_tool.ErrataTool")
def test_slow_handle_shipped_errata(mock_et_constructor, mock_load_errata, mock_app):
    """Test that Brew builds have tags updated correctly when an erratum ships them"""
    erratum_id = 12345
    # The SoftwareBuild model and the slow_fetch_brew_build task use string build IDs
    # But the slow_refresh_brew_build_tags task and the Errata Tool builds_list endpoint
    # use int build IDs, so we pass data from ET directly to the task without conversion
    missing_build_id = 210
    existing_build_id = 12345
    SoftwareBuildFactory(build_type=SoftwareBuild.Type.BREW, build_id=str(existing_build_id))

    mock_et_collector = mock_et_constructor.return_value
    mock_et_collector.get.return_value = {
        "erratum_name": {
            "builds": [
                {"build_nevr": {"id": missing_build_id}},
                {"build_nevr": {"id": existing_build_id}},
            ]
        }
    }

    slow_handle_shipped_errata(erratum_id=erratum_id, erratum_status="SHIPPED_LIVE")

    mock_et_constructor.assert_called_once_with()
    mock_et_collector.get.assert_called_once_with(f"api/v1/erratum/{erratum_id}/builds_list")

    # Missing builds are fetched, existing builds have only their tags refreshed
    mock_fetch_brew_build_call = call(
        "corgi.tasks.brew.slow_fetch_brew_build",
        args=(str(missing_build_id), SoftwareBuild.Type.BREW),
    )
    mock_refresh_brew_build_tags_call = call(
        "corgi.tasks.brew.slow_refresh_brew_build_tags",
        args=(existing_build_id,),
    )
    mock_app.send_task.assert_has_calls(
        [mock_fetch_brew_build_call, mock_refresh_brew_build_tags_call]
    )

    # Taxonomy is always force-saved at the end
    mock_load_errata.delay.assert_called_once_with(str(erratum_id), force_process=True)


def test_slow_handle_shipped_errata_errors():
    """Test that we only process messages for SHIPPED_LIVE errata"""
    erratum_id = 12345
    # Only process messages for SHIPPED_LIVE errata
    # This code should never be hit since we filter the message type
    # using a "selector" on the UMB listener
    with pytest.raises(ValueError):
        slow_handle_shipped_errata(erratum_id=erratum_id, erratum_status="DROPPED_NO_SHIP")


def test_slow_refresh_brew_build_tags():
    """Test that existing builds get their tags refreshed"""
    # The SoftwareBuild model uses string build IDs
    # But koji's listTags method and the Errata Tool builds_list endpoint
    # both use integer build IDs, so we pass ints to this task
    build_id = 12345
    build = SoftwareBuildFactory(
        build_type=SoftwareBuild.Type.BREW,
        build_id=str(build_id),
        meta_attr={"tags": [], "errata_tags": [], "released_errata_tags": []},
    )
    with patch("corgi.tasks.brew.Brew", wraps=Brew) as mock_brew_constructor:
        # Wrap the mocked object, and call its methods directly
        # Unless we override / specify a return value here
        mock_brew_collector = mock_brew_constructor.return_value
        mock_brew_collector.koji_session.listTags.return_value = [
            {"name": tag} for tag in EXISTING_TAGS
        ]
        with patch("corgi.tasks.brew.slow_load_errata") as mock_load_errata:
            slow_refresh_brew_build_tags(build_id=build_id)

        mock_brew_constructor.assert_called_once_with(SoftwareBuild.Type.BREW)
        mock_brew_collector.koji_session.listTags.assert_called_once_with(build_id)
        mock_load_errata.delay.assert_has_calls(
            tuple(call(erratum_id) for erratum_id in CLEAN_ERRATA_TAGS)
        )

    build.refresh_from_db()
    assert build.meta_attr["tags"] == CLEAN_TAGS
    assert build.meta_attr["errata_tags"] == CLEAN_ERRATA_TAGS
    assert build.meta_attr["released_errata_tags"] == CLEAN_RELEASED_TAGS


def test_slow_delete_brew_builds():
    """Test that Brew builds (and their relations, components, and nodes)
    are deleted in Corgi when they're deleted in Brew"""
    build_id = 12345
    srpm_build = SoftwareBuildFactory(
        build_type=SoftwareBuild.Type.BREW,
        build_id=str(build_id),
    )
    # When we delete the build, we should also delete all its components and nodes
    source_rpm = SrpmComponentFactory(software_build=srpm_build)
    srpm_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=source_rpm,
        component=source_rpm,
    )
    source_rpm.save_component_taxonomy()

    # This RPM is only upstream of the source RPM we're deleting
    # So it should get cleaned up
    unshipped_upstream = ComponentFactory(
        type=source_rpm.type,
        namespace=Component.Namespace.UPSTREAM,
        name=source_rpm.name,
        epoch=source_rpm.epoch,
        version=source_rpm.version,
        release="",
        arch="noarch",
        software_build=None,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=srpm_node,
        obj=unshipped_upstream,
        component=unshipped_upstream,
    )
    unshipped_upstream.save_component_taxonomy()

    # Unrelated builds, as well as components they ship, should not be cleaned up
    index_container = ContainerImageComponentFactory()
    oci_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        obj=index_container,
        component=index_container,
    )
    index_container.save_component_taxonomy()

    # This RPM is upstream of both the SRPM and some container
    # So it shouldn't get cleaned up
    # SRPMs don't really have multiple upstreams
    # Just test we don't delete upstreams that are linked to multiple components
    shipped_upstream = ComponentFactory(
        type=source_rpm.type,
        namespace=Component.Namespace.UPSTREAM,
        name=source_rpm.name,
        epoch=source_rpm.epoch,
        version=source_rpm.version,
        release="",
        arch="noarch2",
        software_build=None,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=srpm_node,
        obj=shipped_upstream,
        component=shipped_upstream,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=oci_node,
        obj=shipped_upstream,
        component=shipped_upstream,
    )
    shipped_upstream.save_component_taxonomy()

    # The ARM binary RPM is only provided by the source RPM we're deleting
    # So it should get cleaned up
    unshipped_binary_rpm = ComponentFactory(
        type=source_rpm.type,
        namespace=source_rpm.namespace,
        name=source_rpm.name,
        epoch=source_rpm.epoch,
        version=source_rpm.version,
        release=source_rpm.release,
        arch="aarch64",
        software_build=None,
    )
    unshipped_binary_rpm_node = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_node,
        obj=unshipped_binary_rpm,
        component=unshipped_binary_rpm,
    )
    unshipped_binary_rpm.save_component_taxonomy()

    # This GEM component is provided by both the SRPM
    # and the binary RPM, but all the source components
    # are still part of the same build, so it should be cleaned up
    unshipped_bundled_gem = ComponentFactory(
        type=Component.Type.GEM, namespace=Component.Namespace.UPSTREAM, software_build=None
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=unshipped_binary_rpm_node,
        obj=unshipped_bundled_gem,
        component=unshipped_bundled_gem,
    )
    unshipped_bundled_gem.save_component_taxonomy()

    # The X86 binary RPM is provided by both the SRPM and some container
    # So it shouldn't get cleaned up
    shipped_binary_rpm = ComponentFactory(
        type=source_rpm.type,
        namespace=source_rpm.namespace,
        name=source_rpm.name,
        epoch=source_rpm.epoch,
        version=source_rpm.version,
        release=source_rpm.release,
        arch="x86_64",
        software_build=None,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=srpm_node,
        obj=shipped_binary_rpm,
        component=shipped_binary_rpm,
    )
    ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=oci_node,
        obj=shipped_binary_rpm,
        component=shipped_binary_rpm,
    )
    shipped_binary_rpm.save_component_taxonomy()

    # This unprocessed relation isn't linked to the srpm_build (yet)
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.BREW_TAG,
        build_type=SoftwareBuild.Type.BREW,
        build_id=str(build_id),
    )
    # This processed relation is linked to the srpm_build
    ProductComponentRelationFactory(
        type=ProductComponentRelation.Type.ERRATA,
        build_type=SoftwareBuild.Type.BREW,
        build_id=str(build_id),
        software_build=srpm_build,
    )

    # One SRPM and one container build
    build_count = SoftwareBuild.objects.count()
    assert build_count == 2

    # One tag on both builds automatically created by SoftwareBuildFactory
    build_tag_count = SoftwareBuildTag.objects.count()
    assert build_tag_count == build_count

    # One processed and one unprocessed relation for the SRPM build
    relation_count = ProductComponentRelation.objects.count()
    assert relation_count == 2

    # One root, provided, and upstream component for both builds
    # Plus a bundled component for the source RPM
    component_count = Component.objects.count()
    assert component_count == 7 == (build_count * 3) + 1

    # One tag on each component automatically created by ComponentFactory
    component_tag_count = ComponentTag.objects.count()
    assert component_tag_count == component_count

    # One node for each component above
    # plus 2 extra nodes for the shipped upstream / binary RPM
    # which are linked to both builds / appear in both trees
    node_count = ComponentNode.objects.count()
    assert node_count == component_count + 2

    # One entry in a "component_source" through table
    # for the unshipped binary RPM's sources
    # Two entries for the shipped binary RPM's sources (in two trees)
    # and the bundled Gem's sources (parent and grandparent in one tree)
    component_sources_count = Component.sources.through.objects.count()
    assert component_sources_count == 5

    # Save root / provided component taxonomies after upstream nodes are created
    # in order to add that component to the downstreams property on the upstream component
    # This matches what the code does (create upstream nodes first, save taxonomies last)

    # To set the downstreams directly by saving the upstream component's taxonomy,
    # we'd have to write the inverse function of get_upstreams_pks()
    # and use it with self.downstreams.set() in save_component_taxonomy()
    # This function is very complicated, and any bug would cause
    # downstream components to lose the upstreams we already set correctly
    index_container.save_component_taxonomy()
    source_rpm.save_component_taxonomy()

    # One entry in a "component_upstreams" through table
    # for the source RPM's unshipped_upstream
    # Plus two entries for the container's shipped_upstream
    # Which is in both the SRPM and container trees
    component_upstreams_count = Component.upstreams.through.objects.count()
    assert component_upstreams_count == 3

    # When we try to delete the build, most of the linked models above should also be deleted
    # ProductComponentRelations have a ForeignKey to SoftwareBuild, but it has
    # on_delete=models.SET_NULL instead of on_delete=models.CASCADE
    # So we delete the relations manually
    deleted_count = slow_delete_brew_build(build_id, koji.BUILD_STATES["DELETED"])
    # Don't clean up the index container's build, components, or nodes
    assert (
        deleted_count
        == 24
        == (
            # Delete 2, keep the container build and its tag
            (build_count - 1)
            + (build_tag_count - 1)
            # Delete 2
            + relation_count
            # Delete 4, keep the container root / provided / upstream components
            # even if they were also part of the SRPM build
            + (component_count - 3)
            # Delete 4, keep one entry in the "sources" through table
            # for the root container -> shipped binary RPM
            + (component_sources_count - 1)
            # Delete 2, keep one entry in the "upstreams" through table
            # for the root container -> shipped upstream
            + (component_upstreams_count - 1)
            # Delete 4, keep the container root / provided / upstream nodes and tags
            + (component_tag_count - 3)
            # Delete 6, including 2 SRPM tree nodes linked to the container provided / upstream
            + (node_count - 3)
        )
    )
    assert SoftwareBuild.objects.count() == 1
    assert SoftwareBuildTag.objects.count() == 1
    assert ProductComponentRelation.objects.count() == 0

    components = Component.objects.order_by("created_at")
    assert components.count() == 3
    assert components.first().purl == index_container.purl
    assert components[1].purl == shipped_upstream.purl
    assert components.last().purl == shipped_binary_rpm.purl

    assert ComponentTag.objects.count() == 3
    assert ComponentNode.objects.count() == 3

    nodes = ComponentNode.objects.order_by("created_at")
    assert nodes.count() == 3
    assert nodes.first().purl == index_container.purl
    assert nodes[1].purl == shipped_upstream.purl
    assert nodes.last().purl == shipped_binary_rpm.purl

    # Deleting a nonexistent build will not raise any error
    deleted_count = slow_delete_brew_build(build_id, koji.BUILD_STATES["DELETED"])
    assert deleted_count == 0

    # Only clean up builds in the DELETED state, never the COMPLETE (or another) state
    with pytest.raises(ValueError):
        slow_delete_brew_build(build_id, koji.BUILD_STATES["COMPLETE"])
    with pytest.raises(ValueError):
        slow_delete_brew_build(build_id, koji.BUILD_STATES["FAILED"])


def test_slow_fetch_pnc_sbom():
    """Test fetching SBOMs from PNC"""
    # A typical "SBOM available" UMB message
    with open("tests/data/pnc/sbom_complete.json") as complete_file:
        complete_data = json.load(complete_file)["msg"]

    purl = complete_data["purl"]
    product_config = complete_data["productConfig"]["errataTool"]
    build = complete_data["build"]
    sbom = complete_data["sbom"]

    # Ensure the product variant referenced in the UMB message exists
    # ...
    rhbq_product = CollectorErrataProduct.objects.create(
        et_id=153, name="Red Hat build of Quarkus", short_name="RHBQ"
    )
    rhbq_product_version = CollectorErrataProductVersion.objects.create(
        et_id=1838,
        name="RHEL-8-RHBQ-2.13",
        product=rhbq_product,
        brew_tags=["quarkus-mandrel-22-rhel-8-candidate"],
    )
    CollectorErrataProductVariant.objects.create(
        et_id=4060,
        name="8Base-RHBQ-2.13",
        product_version=rhbq_product_version,
        cpe="cpe:/a:redhat:quarkus:2.13::el8",
    )

    # The SBOM referenced in the UMB message
    with open("tests/data/pnc/pnc_sbom.json") as sbom_file:
        sbom_contents = json.load(sbom_file)

    # Mock response
    response = Mock()
    response.status_code = 200
    response.json.return_value = sbom_contents

    with patch("requests.get", return_value=response) as get_mock:
        slow_fetch_pnc_sbom(purl, product_config, build, sbom)
        get_mock.assert_called_once_with(sbom["link"])

    # Test with an SBOM available message that has a PV that
    # doesn't exist in ET. parse_pnc_sbom shouldn't be called,
    # because the fetch task should log that the PV doesn't exist
    # and end early.
    with patch("corgi.collectors.pnc.parse_pnc_sbom") as parse_sbom_mock:
        with open("tests/data/pnc/sbom_no_variant.json") as complete_file:
            complete_data = json.load(complete_file)["msg"]

        slow_fetch_pnc_sbom(
            complete_data["purl"],
            complete_data["productConfig"]["errataTool"],
            complete_data["build"],
            complete_data["sbom"],
        )

        parse_sbom_mock.assert_not_called()

import logging
import re

from django.utils import dateformat, timezone

from config.celery import app
from corgi.collectors.brew import Brew, BrewBuildTypeNotSupported
from corgi.core.models import Component, ComponentNode, SoftwareBuild
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.errata_tool import load_errata
from corgi.tasks.sca import slow_software_composition_analysis

logger = logging.getLogger(__name__)


@app.task(autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_brew_build(build_id: int, save_product: bool = True):
    logger.info("Fetch brew build called with build id: %s", build_id)
    if SoftwareBuild.objects.filter(build_id=build_id).count() > 0:
        logger.info("Already processed build_id %s", build_id)
        return
    else:
        logger.info("Fetching brew build with build_id: %s", build_id)

    build = Brew().get_component_data(build_id)
    build_meta = build["build_meta"]["build_info"]
    build_meta["corgi_ingest_start_dt"] = dateformat.format(timezone.now(), "Y-m-d H:i:s")
    build_meta["corgi_ingest_status"] = "INPROGRESS"

    softwarebuild, created = SoftwareBuild.objects.get_or_create(
        build_id=build["build_meta"]["build_info"]["build_id"],
        defaults={
            "type": SoftwareBuild.Type.BREW,
            "name": build["meta"]["name"],
            "source": build["build_meta"]["build_info"]["source"],
            "meta_attr": build_meta,
        },
    )
    if not created:
        # If another task starts while this task is downloading data this can result in processing
        # the same build twice, let's just bail out here to save cpu
        logger.warning("SoftwareBuild with build_id %s already existed, not reprocessing", build_id)
        return

    if build["type"] == "rpm":
        root_node = save_srpm(softwarebuild, build)
    elif build["type"] == "image":
        root_node = save_container(softwarebuild, build)
    elif build["type"] == "module":
        root_node = save_module(softwarebuild, build)
    else:
        raise BrewBuildTypeNotSupported(f"Build {build_id} type is not supported: {build['type']}")

    for c in build.get("components", []):
        save_component(c, root_node, softwarebuild)

    # Once we have the full component tree loaded
    softwarebuild.save_component_taxonomy()
    # We don't call save_product_taxonomy by default to allow async call of load_errata task
    # See CORGI-21
    if save_product:
        softwarebuild.save_product_taxonomy()

    # for builds with errata tags set ProductComponentRelation
    # get_component_data always calls _extract_advisory_ids to set tags, but list may be empty
    if not build_meta["errata_tags"]:
        logger.info("no errata tags")
    else:
        if isinstance(build_meta["errata_tags"], str):
            load_errata.delay(build_meta["errata_tags"])
        else:
            for e in build_meta["errata_tags"]:
                load_errata.delay(e)

    if "nested_builds" in build:
        logger.info("Fetching brew builds for %s", build["nested_builds"])
        [slow_fetch_brew_build.delay(build_id) for build_id in build["nested_builds"]]

    logger.info("Requesting software composition analysis for %s", build_id)
    slow_software_composition_analysis.delay(build_id)

    logger.info("Finished fetching brew build: %s", build_id)


def find_package_file_name(sources: list[str]) -> str:
    """Find a packageFileName for a manifest using a list of source filenames from a build system"""
    for source in sources:
        # Use first matching source value that looks like a package
        match = re.match(r"\.(rpm|tar|tgz|zip)", source)
        if match:
            return source
    return ""  # If sources was an empty list, or none of the filenames matched


def save_component(component, parent, softwarebuild=None):
    logger.debug("Called save component with component %s", component)
    component_type = component.pop("type").upper()
    meta = component.get("meta", {})

    node_type = ComponentNode.ComponentNodeType.PROVIDES
    if meta.pop("dev", False):
        node_type = ComponentNode.ComponentNodeType.PROVIDES_DEV

    component_version = meta.pop("version", "")
    if component_type in ["GO-PACKAGE", "GOMOD"]:
        component_type = Component.Type.GOLANG

    elif component_type == "PIP":
        component_type = "PYPI"
    elif component_type not in Component.Type.names:
        logger.warning("Tried to create component with invalid component_type: %s", component_type)
        return

    # Only save softwarebuild for RPM where they are direct children of SRPMs
    # This avoid the situation where only the latest build fetched has the softarebuild associated
    # For example if we were processing a container image with embedded rpms this could be set to
    # the container build id, whereas we want it also to reflect the build id of the RPM build
    if not (
        softwarebuild
        and component_type == Component.Type.RPM
        and parent.obj.type == Component.Type.SRPM
    ):
        softwarebuild = None

    obj, _ = Component.objects.update_or_create(
        type=component_type,
        name=meta.pop("name", ""),
        version=component_version,
        release=meta.pop("release", ""),
        arch=meta.pop("arch", ""),
        defaults={
            "description": meta.pop("description", ""),
            "filename": find_package_file_name(meta.pop("source", [])),
            "license": meta.pop("license", ""),
            "related_url": meta.pop("url", ""),
            "software_build": softwarebuild,
        },
    )

    # Usually component_meta is an empty dict by the time we get here, but if it's not, and we have
    # new keys, add them to the existing meta_attr. Only call save if something has been added
    if meta:
        obj.meta_attr = obj.meta_attr | meta
        obj.save()

    node, _ = obj.cnodes.get_or_create(
        type=node_type,
        parent=parent,
    )
    recurse_components(component, node)


def save_srpm(softwarebuild, build_data) -> ComponentNode:
    obj, created = Component.objects.get_or_create(
        name=build_data["meta"].get("name"),
        type=Component.Type.SRPM,
        arch=build_data["meta"].get("arch", ""),
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        defaults={
            "license": build_data["meta"].get("license", ""),
            "description": build_data["meta"].get("description", ""),
            "software_build": softwarebuild,
            "meta_attr": build_data["meta"],
        },
    )
    node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
    )
    if "url" in build_data["meta"]:
        new_upstream, created = Component.objects.get_or_create(
            type=Component.Type.UPSTREAM,
            name=build_data["meta"].get("name"),
            version=build_data["meta"].get("version", ""),
            # To avoid any future variance of license and related_url
            # set only when initially created
            defaults={
                "description": build_data["meta"].get("description", ""),
                "filename": find_package_file_name(build_data["meta"].get("source", [])),
                "license": build_data["meta"].get("license", ""),
                "related_url": build_data["meta"].get("url", ""),
            },
        )
        new_upstream.cnodes.get_or_create(
            type=ComponentNode.ComponentNodeType.SOURCE,
            parent=node,
        )
    return node


def process_image_components(image):
    builds_to_fetch = set()
    if "rpm_components" in image:
        for rpm in image["rpm_components"]:
            builds_to_fetch.add(rpm["brew_build_id"])
        # TODO save the list of rpms by image to the container meta for reconcilation.
    return builds_to_fetch


def save_container(softwarebuild, build_data) -> ComponentNode:
    obj, created = Component.objects.get_or_create(
        name=build_data["meta"]["name"],
        type=Component.Type.CONTAINER_IMAGE,
        arch="noarch",
        version=build_data["meta"]["version"],
        release=build_data["meta"]["release"],
        defaults={
            "software_build": softwarebuild,
            "meta_attr": build_data["meta"],
        },
    )
    root_node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
    )

    if "upstream_go_modules" in build_data["meta"]:
        for module in build_data["meta"]["upstream_go_modules"]:
            new_upstream, created = Component.objects.get_or_create(
                type=Component.Type.GOLANG,
                name=module,
                # the upstream commit is included in the dist-git commit history, but is not
                # exposed anywhere in the brew data that I can find
                version="",
            )
            new_upstream.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=root_node,
            )

    if "image_components" in build_data:
        for image in build_data["image_components"]:
            obj, created = Component.objects.get_or_create(
                name=image["meta"].pop("name"),
                type=Component.Type.CONTAINER_IMAGE,
                arch=image["meta"].pop("arch"),
                version=image["meta"].pop("version"),
                release=image["meta"].pop("release"),
                defaults={
                    "software_build": softwarebuild,
                    "meta_attr": image["meta"],
                },
            )
            image_arch_node, _ = obj.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=root_node,
            )

            if "rpm_components" in image:
                for rpm in image["rpm_components"]:
                    save_component(rpm, image_arch_node)
                    # SRPMs are loaded using nested_builds

    if "sources" in build_data:
        for source in build_data["sources"]:
            new_upstream, created = Component.objects.get_or_create(
                type=Component.Type.UPSTREAM,
                name=source["meta"].pop("name"),
                version=source["meta"].pop("version"),
                defaults={"meta_attr": source["meta"]},
            )
            upstream_node, _ = new_upstream.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=root_node,
            )
            # Collect the Cachito dependencies
            recurse_components(source, upstream_node)
    return root_node


def recurse_components(component, parent):
    if not parent:
        logger.warning(f"Failed to create ComponentNode for component: {component}")
    else:
        if "components" in component:
            for c in component["components"]:
                save_component(c, parent)


def save_module(softwarebuild, build_data) -> ComponentNode:
    obj, created = Component.objects.get_or_create(
        name=build_data["meta"]["name"],
        type=Component.Type.RHEL_MODULE,
        arch=build_data["meta"].get("arch", ""),
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        defaults={
            "license": build_data["meta"].get("license", ""),
            "description": build_data["meta"].get("description", ""),
            "software_build": softwarebuild,
            "meta_attr": build_data["meta"]["components"],
        },
    )
    node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
    )
    # TODO: add upstream if exists
    # TODO: recurse components from build_data["meta"]["components"]

    return node

import logging
import re
from datetime import timedelta
from typing import Optional

from celery_singleton import Singleton
from django.conf import settings
from django.db.models import QuerySet
from django.utils import dateformat, dateparse, timezone
from django.utils.timezone import make_aware

from config.celery import app
from corgi.collectors.brew import Brew, BrewBuildTypeNotSupported
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.errata_tool import slow_load_errata
from corgi.tasks.sca import slow_software_composition_analysis

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_brew_build(build_id: int, save_product: bool = True, force_process: bool = False):
    logger.info("Fetch brew build called with build id: %s", build_id)
    try:
        softwarebuild = SoftwareBuild.objects.get(build_id=build_id)
    except SoftwareBuild.DoesNotExist:
        pass
    else:
        if not force_process:
            logger.info("Already processed build_id %s, only saving product taxonomy", build_id)
            softwarebuild.save_product_taxonomy()
            return
        else:
            logger.info("Fetching brew build with build_id: %s", build_id)

    try:
        component = Brew().get_component_data(build_id)
    except BrewBuildTypeNotSupported as exc:
        logger.warning(str(exc))
        return

    if not component:
        logger.info("No data fetched for build %s from Brew, exiting...", build_id)
        return

    build_meta = component["build_meta"]["build_info"]
    build_meta["corgi_ingest_start_dt"] = dateformat.format(timezone.now(), "Y-m-d H:i:s")
    build_meta["corgi_ingest_status"] = "INPROGRESS"

    completion_time = build_meta.get("completion_time", "")
    if completion_time:
        dt = dateparse.parse_datetime(completion_time.split(".")[0])
        if dt:
            completion_dt = make_aware(dt)
        else:
            logger.info("Could not parse completion_time for build %s", build_id)
            return
    else:
        logger.info("No completion_time for build %s", build_id)
        return

    softwarebuild, created = SoftwareBuild.objects.get_or_create(
        build_id=component["build_meta"]["build_info"]["build_id"],
        defaults={
            "type": SoftwareBuild.Type.BREW,
            "name": component["meta"]["name"],
            "source": component["build_meta"]["build_info"]["source"],
            "meta_attr": build_meta,
        },
        completion_time=completion_dt,
    )

    if not force_process and not created:
        # If another task starts while this task is downloading data this can result in processing
        # the same build twice, let's just bail out here to save cpu
        logger.warning("SoftwareBuild with build_id %s already existed, not reprocessing", build_id)
        return

    if component["type"] == Component.Type.RPM:
        root_node = save_srpm(softwarebuild, component)
    elif component["type"] == Component.Type.CONTAINER_IMAGE:
        root_node = save_container(softwarebuild, component)
    elif component["type"] == Component.Type.RPMMOD:
        root_node = save_module(softwarebuild, component)
    else:
        logger.warning(f"Build {build_id} type is not supported: {component['type']}")
        return

    for c in component.get("components", []):
        save_component(c, root_node, softwarebuild)

    # We don't call save_product_taxonomy by default to allow async call of slow_load_errata task
    # See CORGI-21
    if save_product:
        softwarebuild.save_product_taxonomy()

    # for builds with errata tags set ProductComponentRelation
    # get_component_data always calls _extract_advisory_ids to set tags, but list may be empty
    if not build_meta["errata_tags"]:
        logger.info("no errata tags")
    else:
        if isinstance(build_meta["errata_tags"], str):
            slow_load_errata.delay(build_meta["errata_tags"])
        else:
            for e in build_meta["errata_tags"]:
                slow_load_errata.delay(e)

    build_ids = component.get("nested_builds", ())
    logger.info("Fetching brew builds for %s", build_ids)
    for b_id in build_ids:
        logger.info("Requesting fetch of nested build: %s", b_id)
        slow_fetch_brew_build.delay(b_id)

    logger.info("Requesting software composition analysis for %s", build_id)
    slow_software_composition_analysis.delay(build_id)

    logger.info("Finished fetching brew build: %s", build_id)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_modular_build(build_id: str, force_process: bool = False) -> None:
    logger.info("Fetch modular build called with build id: %s", build_id)
    rhel_module_data = Brew.fetch_rhel_module(build_id)
    # Some compose build_ids in the relations table will be for SRPMs, skip those here
    if not rhel_module_data:
        logger.info("No module data fetched for build %s from Brew, exiting...", build_id)
        slow_fetch_brew_build.delay(int(build_id), force_process=force_process)
        return
    # TODO: Should we use update_or_create here?
    #  We don't currently handle reprocessing a modular build
    # Note: module builds don't include arch information, only the individual RPMs that make up a
    # module are built for specific architectures.
    obj, created = Component.objects.get_or_create(
        name=rhel_module_data["meta"]["name"],
        type=rhel_module_data["type"],
        version=rhel_module_data["meta"]["version"],
        release=rhel_module_data["meta"]["release"],
        defaults={
            # This gives us an indication as to which task (this or fetch_brew_build)
            # last processed the module
            "meta_attr": rhel_module_data["analysis_meta"],
        },
    )
    # This should result in a lookup if fetch_brew_build has already processed this module.
    # Likewise if fetch_brew_build processes the module subsequently we should not create
    # a new ComponentNode, instead the same one will be looked up and used as the root node
    node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )
    for c in rhel_module_data.get("components", []):
        # Request fetch of the SRPM build_ids here to ensure software_builds are created and linked
        # to the RPM components. We don't link the SRPM into the tree because some of it's RPMs
        # might not be included in the module
        if "brew_build_id" in c:
            slow_fetch_brew_build.delay(c["brew_build_id"])
        save_component(c, node)
    slow_fetch_brew_build.delay(int(build_id), force_process=force_process)
    logger.info("Finished fetching modular build: %s", build_id)


def find_package_file_name(sources: list[str]) -> str:
    """Find a packageFileName for a manifest using a list of source filenames from a build system"""
    for source in sources:
        # Use first matching source value that looks like a package
        match = re.match(r"\.(?:rpm|tar|tgz|zip)", source)
        if match:
            return source
    return ""  # If sources was an empty list, or none of the filenames matched


def save_component(
    component: dict, parent: ComponentNode, softwarebuild: Optional[SoftwareBuild] = None
):
    logger.debug("Called save component with component %s", component)
    component_type = component.pop("type").upper()
    meta = component.get("meta", {})

    node_type = ComponentNode.ComponentNodeType.PROVIDES
    if meta.pop("dev", False):
        node_type = ComponentNode.ComponentNodeType.PROVIDES_DEV

    component_version = meta.pop("version", "")
    if component_type in ("GO-PACKAGE", "GOMOD"):
        component_type = Component.Type.GOLANG

    elif component_type == "PIP":
        component_type = "PYPI"

    elif component_type not in Component.Type.values:
        logger.warning("Tried to create component with invalid component_type: %s", component_type)
        return

    # Only save softwarebuild for RPM where they are direct children of SRPMs
    # This avoids the situation where only the latest build fetched has the softarebuild associated
    # For example if we were processing a container image with embedded rpms this could be set to
    # the container build id, whereas we want it also to reflect the build id of the RPM build
    if not (softwarebuild and parent.obj is not None and parent.obj.is_srpm()):
        softwarebuild = None

    # Handle case when key is present but value is None
    related_url = meta.pop("url", "")
    if related_url is None:
        related_url = ""
    obj, _ = Component.objects.update_or_create(
        type=component_type,
        name=meta.pop("name", ""),
        version=component_version,
        release=meta.pop("release", ""),
        arch=meta.pop("arch", ""),
        defaults={
            "description": meta.pop("description", ""),
            "filename": find_package_file_name(meta.pop("source", [])),
            "license_declared_raw": meta.pop("license", ""),
            "namespace": component.get("namespace", ""),
            "related_url": related_url,
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
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )
    recurse_components(component, node)


def save_srpm(softwarebuild: SoftwareBuild, build_data: dict) -> ComponentNode:
    obj, created = Component.objects.get_or_create(
        name=build_data["meta"].get("name"),
        type=build_data["type"],
        arch=build_data["meta"].get("arch", ""),
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        defaults={
            "license_declared_raw": build_data["meta"].get("license", ""),
            "description": build_data["meta"].get("description", ""),
            "software_build": softwarebuild,
            "meta_attr": build_data["meta"],
            "namespace": build_data["namespace"],
        },
    )
    node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )
    # Handle case when key is present but value is None
    related_url = build_data["meta"].get("url", "")
    if related_url:
        new_upstream, created = Component.objects.get_or_create(
            type=build_data["type"],
            namespace=Component.Namespace.UPSTREAM,
            name=build_data["meta"].get("name"),
            version=build_data["meta"].get("version", ""),
            # To avoid any future variance of license_declared and related_url
            # set only when initially created
            defaults={
                "description": build_data["meta"].get("description", ""),
                "filename": find_package_file_name(build_data["meta"].get("source", [])),
                "license_declared_raw": build_data["meta"].get("license", ""),
                "related_url": related_url,
            },
        )
        new_upstream.cnodes.get_or_create(
            type=ComponentNode.ComponentNodeType.SOURCE,
            parent=node,
            purl=new_upstream.purl,
            defaults={
                "object_id": new_upstream.pk,
                "obj": new_upstream,
            },
        )
    return node


def process_image_components(image):
    builds_to_fetch = set()
    if "rpm_components" in image:
        for rpm in image["rpm_components"]:
            builds_to_fetch.add(rpm["brew_build_id"])
        # TODO save the list of rpms by image to the container meta for reconcilation.
    return builds_to_fetch


def save_container(softwarebuild: SoftwareBuild, build_data: dict) -> ComponentNode:
    obj, created = Component.objects.get_or_create(
        name=build_data["meta"]["name"],
        type=build_data["type"],
        arch="noarch",
        version=build_data["meta"]["version"],
        release=build_data["meta"]["release"],
        defaults={
            "software_build": softwarebuild,
            "meta_attr": build_data["meta"],
            "namespace": build_data.get("namespace", ""),
        },
    )
    root_node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )

    if "upstream_go_modules" in build_data["meta"]:
        for module in build_data["meta"]["upstream_go_modules"]:
            new_upstream, created = Component.objects.get_or_create(
                type=Component.Type.GOLANG,
                name=module,
                # the upstream commit is included in the dist-git commit history, but is not
                # exposed anywhere in the brew data that I can find
                version="",
                defaults={"namespace": Component.Namespace.UPSTREAM},
            )
            new_upstream.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=root_node,
                purl=new_upstream.purl,
                defaults={
                    "object_id": new_upstream.pk,
                    "obj": new_upstream,
                },
            )

    if "image_components" in build_data:
        for image in build_data["image_components"]:
            obj, created = Component.objects.get_or_create(
                name=image["meta"].pop("name"),
                type=image["type"],
                arch=image["meta"].pop("arch"),
                version=image["meta"].pop("version"),
                release=image["meta"].pop("release"),
                defaults={
                    "software_build": softwarebuild,
                    "meta_attr": image["meta"],
                    "namespace": image.get("namespace", ""),
                },
            )
            image_arch_node, _ = obj.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=root_node,
                purl=obj.purl,
                defaults={
                    "object_id": obj.pk,
                    "obj": obj,
                },
            )

            if "rpm_components" in image:
                for rpm in image["rpm_components"]:
                    save_component(rpm, image_arch_node)
                    # SRPMs are loaded using nested_builds

    if "sources" in build_data:
        for source in build_data["sources"]:
            # Handle case when key is present but value is None
            related_url = source["meta"].pop("url", "")
            if related_url is None:
                related_url = ""
            new_upstream, created = Component.objects.get_or_create(
                type=source["type"],
                name=source["meta"].pop("name"),
                version=source["meta"].pop("version"),
                defaults={
                    "meta_attr": source["meta"],
                    "related_url": related_url,
                    "namespace": Component.Namespace.UPSTREAM,
                },
            )
            upstream_node, _ = new_upstream.cnodes.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=root_node,
                purl=new_upstream.purl,
                defaults={
                    "object_id": new_upstream.pk,
                    "obj": new_upstream,
                },
            )
            # Collect the Cachito dependencies
            recurse_components(source, upstream_node)
    return root_node


def recurse_components(component: dict, parent: ComponentNode):
    if not parent:
        logger.warning(f"Failed to create ComponentNode for component: {component}")
    else:
        if "components" in component:
            for child in component["components"]:
                save_component(child, parent)


def save_module(softwarebuild, build_data) -> ComponentNode:
    """Upstreams are not created because modules have no related source code. They are a
    collection of RPMs from other SRPMS. The upstreams can be looked up from all the RPM children.
    No child components are created here because we don't have enough data in Brew to determine
    the relationships. We create the relationships using data from RHEL_COMPOSE, or RPM repository
    See CORGI-200, and CORGI-163"""
    meta_attr = build_data["meta"]["meta_attr"]
    meta_attr.update(build_data["analysis_meta"])
    obj, created = Component.objects.update_or_create(
        name=build_data["meta"]["name"],
        type=build_data["type"],
        arch=build_data["meta"].get("arch", ""),
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        defaults={
            "license_declared_raw": build_data["meta"].get("license", ""),
            "description": build_data["meta"].get("description", ""),
            "software_build": softwarebuild,
            "meta_attr": meta_attr,
            "namespace": build_data["namespace"],
        },
    )
    node, _ = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )

    return node


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def load_brew_tags() -> None:
    for ps in ProductStream.objects.get_queryset():
        brew = Brew()
        for brew_tag, inherit in ps.brew_tags.items():
            # Always load all builds in tag when saving relations
            # TODO: Use _create_relations here and in other places
            builds = brew.get_builds_with_tag(brew_tag, inherit=inherit, latest=False)
            no_of_created = 0
            for build in builds:
                _, created = ProductComponentRelation.objects.get_or_create(
                    external_system_id=brew_tag,
                    product_ref=ps.name,
                    build_id=build,
                    defaults={"type": ProductComponentRelation.Type.BREW_TAG},
                )
                if created:
                    no_of_created += 1
            logger.info("Saving %s new builds for %s", no_of_created, brew_tag)


def fetch_modular_builds(relations_query: QuerySet, force_process: bool = False) -> None:
    for build_id in relations_query:
        slow_fetch_modular_build.delay(build_id, force_process=force_process)


def fetch_unprocessed_relations(
    relation_type: ProductComponentRelation.Type,
    created_since: timedelta,
    force_process: bool = False,
) -> int:
    relations_query = ProductComponentRelation.objects.filter(type=relation_type)
    if created_since:
        created_during = timezone.now() - created_since
        relations_query = relations_query.filter(created_at__gte=created_during)
    # batch process to avoid exhausting the memory limit for the pod
    distinct_ids = relations_query.values_list("build_id", flat=True).distinct()
    relation_count = distinct_ids.count()
    logger.info("Found %s %s relations", relation_count, relation_type)
    offset = 0
    limit = 10000
    processed_builds = 0
    while offset < relation_count:
        logger.info("Processing CDN relations with offset %s and limit %s", offset, limit)
        for build_id in distinct_ids[offset : offset + limit]:
            if not build_id:
                # build_id defaults to "" and int() will fail in this case
                continue
            if not SoftwareBuild.objects.filter(build_id=int(build_id)).exists():
                logger.info("Processing CDN relation build with id: %s", build_id)
                slow_fetch_modular_build.delay(build_id, force_process=force_process)
                processed_builds += 1
        offset = offset + limit
    return processed_builds


@app.task(
    base=Singleton,
    autorety_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def fetch_unprocessed_brew_tag_relations(
    force_process: bool = False, created_since: int = 2
) -> int:
    return fetch_unprocessed_relations(
        ProductComponentRelation.Type.BREW_TAG,
        force_process=force_process,
        created_since=timedelta(days=created_since),
    )

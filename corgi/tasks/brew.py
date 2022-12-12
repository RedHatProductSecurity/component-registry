import re
from typing import Optional

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.models import QuerySet
from django.utils import dateformat, dateparse, timezone

from config.celery import app
from corgi.collectors.brew import Brew, BrewBuildTypeNotSupported
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, get_last_success_for_task
from corgi.tasks.errata_tool import slow_load_errata
from corgi.tasks.sca import cpu_software_composition_analysis

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_build_product_taxonomy(build_id: int) -> None:
    """Helper method to avoid timeouts in Brew / SCA tasks due to slow taxonomy-saving"""
    logger.info(f"save_build_product_taxonomy called for build: {build_id}")
    software_build = SoftwareBuild.objects.get(build_id=build_id)
    logger.info(f"Saving product taxonomy for build: {build_id}")
    software_build.save_product_taxonomy()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_brew_build(build_id: int, save_product: bool = True, force_process: bool = False):
    """Fetch a particular build_id from Brew, optionally overwriting any existing data"""
    start_time = timezone.now()
    logger.info(f"Fetch brew build called with build id: {build_id}, time={start_time}")

    try:
        logger.info(f"Looking up build id: {build_id}, time={timezone.now() - start_time}")
        softwarebuild = SoftwareBuild.objects.get(build_id=build_id)
        logger.info(f"Looking up build id finished: {build_id}, time={timezone.now() - start_time}")

        if not force_process:
            logger.info(
                f"Already processed build_id: {build_id}, time={timezone.now() - start_time}"
            )
            if save_product:
                logger.info(
                    f"Saving product taxonomy: {build_id}, time={timezone.now() - start_time}"
                )
                save_build_product_taxonomy.delay(build_id)
                logger.info(
                    f"Saving product taxonomy finished: "
                    f"{build_id}, time={timezone.now() - start_time}"
                )
            return
        else:
            logger.info(
                f"Fetching brew build with build_id again: "
                f"{build_id}, time={timezone.now() - start_time}"
            )
    except SoftwareBuild.DoesNotExist:
        logger.info(
            f"Fetching brew build with build_id for the first time: "
            f"{build_id}, time={timezone.now() - start_time}"
        )

    try:
        logger.info(f"Instantiating brew class: time={timezone.now() - start_time}")
        brew = Brew()
        logger.info(f"Instantiating brew class finished: time={timezone.now() - start_time}")

        logger.info(
            f"Getting component data for build id: {build_id}, time={timezone.now() - start_time}"
        )
        component = brew.get_component_data(build_id, start_time=start_time)
        logger.info(
            f"Getting component data for build id finished: "
            f"{build_id}, time={timezone.now() - start_time}"
        )
    except BrewBuildTypeNotSupported as exc:
        logger.warning(
            f"Getting component data for build id failed: "
            f"{exc}, time={timezone.now() - start_time}"
        )
        return

    if not component:
        logger.info(
            f"No data fetched from Brew for build: {build_id}, time={timezone.now() - start_time}"
        )
        return

    build_meta = component["build_meta"]["build_info"]
    build_meta["corgi_ingest_start_dt"] = dateformat.format(start_time, "Y-m-d H:i:s")
    build_meta["corgi_ingest_status"] = "INPROGRESS"

    completion_time = build_meta.get("completion_time", "")
    if not completion_time:
        logger.info(f"No completion_time for build: {build_id}, time={timezone.now() - start_time}")
        return

    dt = dateparse.parse_datetime(completion_time.split(".")[0])
    if not dt:
        logger.info(
            f"Could not parse completion_time for build: "
            f"{build_id}, time={timezone.now() - start_time}"
        )
        return

    completion_dt = timezone.make_aware(dt)

    logger.info(
        f"Looking up or saving data for build: {build_id}, time={timezone.now() - start_time}"
    )
    softwarebuild, created = SoftwareBuild.objects.get_or_create(
        build_id=build_meta.pop("build_id"),
        defaults={
            "type": SoftwareBuild.Type.BREW,
            "name": component["meta"]["name"],
            "source": build_meta.pop("source"),
            "meta_attr": build_meta,
        },
        completion_time=completion_dt,
    )
    logger.info(
        f"Looking up or saving data for build finished: "
        f"{build_id}, time={timezone.now() - start_time}"
    )

    if not force_process and not created:
        # If another task starts while this task is downloading data this can result in processing
        # the same build twice, let's just bail out here to save cpu
        logger.warning(
            f"SoftwareBuild already existed, not reprocessing: "
            f"{build_id}, time={timezone.now() - start_time}"
        )
        return

    logger.info(
        f"Saving ({component['type']}) component data for build id: "
        f"{build_id}, time={timezone.now() - start_time}"
    )
    if component["type"] == Component.Type.RPM:
        root_node = save_srpm(softwarebuild, component, start_time=start_time)
    elif component["type"] == Component.Type.CONTAINER_IMAGE:
        root_node = save_container(softwarebuild, component, start_time=start_time)
    elif component["type"] == Component.Type.RPMMOD:
        root_node = save_module(softwarebuild, component, start_time=start_time)
    else:
        logger.warning(
            f"Build {build_id} type is not supported: "
            f"{component['type']}, time={timezone.now() - start_time}"
        )
        return
    logger.info(
        f"Saving ({component['type']}) component data for build id finished: "
        f"{build_id}, time={timezone.now() - start_time}"
    )

    for c in component.get("components", ()):
        logger.info(
            f"Saving ({c['type']}) child component data: "
            f"{build_id}, time={timezone.now() - start_time}"
        )
        save_component(c, root_node, softwarebuild, start_time=start_time)
        logger.info(
            f"Saving ({c['type']}) child component data finished: "
            f"{build_id}, time={timezone.now() - start_time}"
        )

    # TODO: This comment is outdated and seems wrong, could be the reason it's slow
    # We don't call save_product_taxonomy by default to allow async call of slow_load_errata task
    # See CORGI-21
    if save_product:
        logger.info(f"Saving product taxonomy: {build_id}, time={timezone.now() - start_time}")
        save_build_product_taxonomy.delay(build_id)
        logger.info(
            f"Saving product taxonomy finished: {build_id}, time={timezone.now() - start_time}"
        )

    # for builds with errata tags set ProductComponentRelation
    # get_component_data always calls _extract_advisory_ids to set tags, but list may be empty
    errata_tags = build_meta.get("errata_tags")
    if not errata_tags:
        logger.info(f"No errata tags for build: {build_id}, time={timezone.now() - start_time}")
    elif isinstance(errata_tags, str):
        logger.info(
            f"Loading str errata tag for build: "
            f"{build_id}, {errata_tags}, time={timezone.now() - start_time}"
        )
        slow_load_errata.delay(build_meta["errata_tags"])
    else:
        logger.info(
            f"Loading list of errata tags for build: {build_id}, time={timezone.now() - start_time}"
        )
        for errata_tag in errata_tags:
            logger.info(
                f"Requesting load of nested errata tag: "
                f"{errata_tag}, time={timezone.now() - start_time}"
            )
            slow_load_errata.delay(errata_tag)
        logger.info(
            f"Finished loading list of errata tags for build: "
            f"{build_id}, time={timezone.now() - start_time}"
        )

    nested_build_ids = component.get("nested_builds", ())
    logger.info(
        f"Fetching nested brew builds for build: {build_id}, time={timezone.now() - start_time}"
    )
    for nested_build_id in nested_build_ids:
        logger.info(
            f"Requesting fetch of nested build: "
            f"{nested_build_id}, time={timezone.now() - start_time}"
        )
        slow_fetch_brew_build.delay(nested_build_id)
    logger.info(
        f"Finished fetching nested brew builds for build: "
        f"{build_id}, time={timezone.now() - start_time}"
    )

    if settings.SCA_ENABLED:
        logger.info(
            f"Requesting software composition analysis for build: "
            f"{build_id}, time={timezone.now() - start_time}"
        )
        cpu_software_composition_analysis.delay(build_id)

    logger.info(f"Finished fetching brew build: {build_id}, time={timezone.now() - start_time}")


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
    meta = rhel_module_data["meta"]
    obj, created = Component.objects.get_or_create(
        name=meta.pop("name"),
        type=rhel_module_data["type"],
        version=meta.pop("version"),
        release=meta.pop("release"),
        defaults={
            # Any remaining meta keys are recorded in meta_attr
            "meta_attr": meta,
        },
    )
    # This should result in a lookup if slow_fetch_brew_build has already processed this module.
    # Likewise if slow_fetch_brew_build processes the module subsequently we should not create
    # a new ComponentNode, instead the same one will be looked up and used as the root node
    node, _ = ComponentNode.objects.get_or_create(
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
        match = re.search(r"\.(?:rpm|tar|tgz|zip)", source)
        if match:
            return source
    return ""  # If sources was an empty list, or none of the filenames matched


def save_component(
    component: dict,
    parent: ComponentNode,
    softwarebuild: Optional[SoftwareBuild] = None,
    start_time: Optional[timezone.datetime] = None,
):
    logger.debug("Called save component with component %s", component)
    component_type = component["type"].upper()
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
    # This avoids the situation where only the latest build fetched has the softwarebuild associated
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
            "filename": find_package_file_name(meta.pop("source_files", [])),
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

    node, _ = ComponentNode.objects.get_or_create(
        type=node_type,
        parent=parent,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )
    recurse_components(component, node)


def save_srpm(
    softwarebuild: SoftwareBuild, build_data: dict, start_time: Optional[timezone.datetime] = None
) -> ComponentNode:
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
    node, _ = ComponentNode.objects.get_or_create(
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
                "filename": find_package_file_name(build_data["meta"].get("source_files", [])),
                "license_declared_raw": build_data["meta"].get("license", ""),
                "related_url": related_url,
            },
        )
        ComponentNode.objects.get_or_create(
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


def save_container(
    softwarebuild: SoftwareBuild, build_data: dict, start_time: Optional[timezone.datetime] = None
) -> ComponentNode:
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
    root_node, _ = ComponentNode.objects.get_or_create(
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
            ComponentNode.objects.get_or_create(
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
            image_arch_node, _ = ComponentNode.objects.get_or_create(
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
            upstream_node, _ = ComponentNode.objects.get_or_create(
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


def save_module(
    softwarebuild, build_data, start_time: Optional[timezone.datetime] = None
) -> ComponentNode:
    """Upstreams are not created because modules have no related source code. They are a
    collection of RPMs from other SRPMS. The upstreams can be looked up from all the RPM children.
    No child components are created here because we don't have enough data in Brew to determine
    the relationships. We create the relationships using data from RHEL_COMPOSE, or RPM repository
    See CORGI-200, and CORGI-163"""
    meta_attr = build_data["meta"]["meta_attr"]
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
    node, _ = ComponentNode.objects.get_or_create(
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
    created_since: timezone.datetime,
    force_process: bool = False,
) -> int:
    relations_query = (
        ProductComponentRelation.objects.filter(type=relation_type, created_at__gte=created_since)
        .values_list("build_id", flat=True)
        .distinct()
    )
    logger.info(f"Processing relations of type {relation_type}")
    processed_builds = 0
    for build_id in relations_query.iterator():
        if not build_id:
            # build_id defaults to "" and int() will fail in this case
            continue
        if not SoftwareBuild.objects.filter(build_id=int(build_id)).exists():
            logger.info("Processing CDN relation build with id: %s", build_id)
            slow_fetch_modular_build.delay(build_id, force_process=force_process)
            processed_builds += 1
    return processed_builds


@app.task(
    base=Singleton,
    autorety_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def fetch_unprocessed_brew_tag_relations(
    force_process: bool = False, days_created_since: int = 0
) -> int:
    if days_created_since:
        created_dt = timezone.now() - timezone.timedelta(days=days_created_since)
    else:
        created_dt = get_last_success_for_task(
            "corgi.tasks.brew.fetch_unprocessed_brew_tag_relations"
        )
    return fetch_unprocessed_relations(
        ProductComponentRelation.Type.BREW_TAG,
        force_process=force_process,
        created_since=created_dt,
    )

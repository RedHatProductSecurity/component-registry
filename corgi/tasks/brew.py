from datetime import datetime, timedelta
from typing import Optional

import koji
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import dateformat, dateparse, timezone
from django.utils.timezone import make_aware

from config.celery import app
from corgi.collectors.brew import ADVISORY_REGEX, Brew, BrewBuildTypeNotSupported
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.common import (
    BUILD_TYPE,
    RETRY_KWARGS,
    RETRYABLE_ERRORS,
    create_relations,
    get_last_success_for_task,
)
from corgi.tasks.errata_tool import slow_load_errata
from corgi.tasks.sca import cpu_software_composition_analysis

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_brew_build(
    build_id: str,
    build_type: str = BUILD_TYPE,
    save_product: bool = True,
    force_process: bool = False,
):
    logger.info("Fetch brew build called with build id: %s", build_id)

    try:
        softwarebuild = SoftwareBuild.objects.get(build_id=build_id, build_type=build_type)
    except SoftwareBuild.DoesNotExist:
        pass
    else:
        if not force_process:
            logger.info("Already processed build_id %s", build_id),
            if save_product:
                logger.info("Only saving product taxonomy for build_id %s", build_id)
                softwarebuild.save_product_taxonomy()
                # Should only be one root component / node per build
                for root_component in softwarebuild.components.get_queryset():
                    root_node = root_component.cnodes.get()
                    _save_component_taxonomy_for_tree(root_node)
            return
        else:
            logger.info("Fetching brew build with build_id: %s", build_id)

    try:
        component = Brew(build_type).get_component_data(int(build_id))
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
            raise ValueError(f"Could not parse completion_time for build {build_id}")
    else:
        # Build has no timestamp even though it's in COMPLETE state?
        # This really shouldn't happen
        raise ValueError(f"No completion_time for build {build_id}")

    # TODO: Should we update_or_create to pick up changed build_meta?
    #  Sometimes builds are deleted in Brew
    #  In that case, don't wipe out data - test this
    softwarebuild, created = SoftwareBuild.objects.get_or_create(
        build_id=build_id,
        build_type=build_type,
        defaults={
            "source": build_meta.pop("source"),
            "meta_attr": build_meta,
            "name": component["meta"]["name"],
        },
        completion_time=completion_dt,
    )
    if created:
        # Create foreign key from Relations to the new SoftwareBuild, where they don't already exist
        ProductComponentRelation.objects.filter(
            build_id=build_id, build_type=build_type, software_build__isnull=True
        ).update(software_build=softwarebuild)

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

    for child_component in component.get("components", []):
        save_component(child_component, root_node, softwarebuild)

    # for builds with any tag, check if the tag is used for product stream relations, and create the
    # relations if so.
    if not build_meta["tags"]:
        logger.info("no brew tags")
    else:
        new_relations = load_brew_tags(softwarebuild, build_meta["tags"])
        logger.info(f"Created {new_relations} for brew tags in {build_type}:{build_id}")

    # Allow async call of slow_load_errata task, see CORGI-21
    if save_product:
        softwarebuild.save_product_taxonomy()
        # Save taxonomies for all components in this tree
        # Saving only the root component taxonomy from softwarebuild.components is not enough
        _save_component_taxonomy_for_tree(root_node)

    # for builds with errata tags set ProductComponentRelation
    # get_component_data always calls _extract_advisory_ids to set tags, but list may be empty
    if not build_meta["errata_tags"]:
        logger.info("no errata tags")
    else:
        for e in build_meta["errata_tags"]:
            slow_load_errata.delay(e, force_process=force_process)

    build_ids = component.get("nested_builds", ())
    logger.info("Fetching brew builds for (%s, %s)", build_ids, build_type)
    for b_id in build_ids:
        logger.info("Requesting fetch of nested build: (%s, %s)", b_id, build_type)
        slow_fetch_brew_build.delay(
            b_id, build_type, save_product=save_product, force_process=force_process
        )

    logger.info("Requesting software composition analysis for %s", softwarebuild.pk)
    if settings.SCA_ENABLED:
        cpu_software_composition_analysis.delay(str(softwarebuild.pk), force_process=force_process)

    logger.info("Finished fetching brew build: (%s, %s)", build_id, build_type)


def _save_component_taxonomy_for_tree(root_node: ComponentNode) -> None:
    """Call node.obj.save_component_taxonomy() for all
    root and upstream components in some build,
    all components provided by the root components,
    all components provided by those provided components, and so on
    """
    # This handles the case when a build has many levels of nested components
    # e.g. for an index container (root component) linked to a build
    # there are architecture-specific containers not linked to the build
    # provided components, e.g. Go modules of those containers, which are not linked to the build
    # children of the provided components, e.g. Go packages and dev_provided components
    # as found in some Cachito manifest for the build, which are not linked to the build
    # We have to save the taxonomy for all of them, not just the root components
    root_node.obj.save_component_taxonomy()  # type: ignore[union-attr]

    # TODO: We can make this faster with a ForeignKey to Component
    #  instead of a GenericForeignKey
    #  That will also fix the silly mypy error above we're ignoring
    for descendant in root_node.get_descendants().iterator():
        descendant.obj.save_component_taxonomy()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_modular_build(build_id: str, force_process: bool = False) -> None:
    logger.info("Fetch modular build called with build id: %s", build_id)
    rhel_module_data = Brew.fetch_rhel_module(build_id)
    # Some compose build_ids in the relations table will be for SRPMs, skip those here
    if not rhel_module_data:
        logger.info("No module data fetched for build %s from Brew, exiting...", build_id)
        slow_fetch_brew_build.delay(build_id, force_process=force_process)
        return
    # Note: module builds don't include arch information, only the individual RPMs that make up a
    # module are built for specific architectures.
    # TODO: Merge below with similar logic in save_module() if possible
    meta = rhel_module_data["meta"]
    obj, created = Component.objects.update_or_create(
        type=rhel_module_data["type"],
        name=meta.pop("name"),
        version=meta.pop("version"),
        release=meta.pop("release"),
        arch="noarch",
        defaults={
            # Any remaining meta keys are recorded in meta_attr
            "meta_attr": meta,
            "namespace": Component.Namespace.REDHAT,
        },
    )
    # This should result in a lookup if slow_fetch_brew_build has already processed this module.
    # Likewise if slow_fetch_brew_build processes the module subsequently we should not create
    # a new ComponentNode, instead the same one will be looked up and used as the root node
    node = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)

    for c in rhel_module_data.get("components", []):
        # Request fetch of the SRPM build_ids here to ensure software_builds are created and linked
        # to the RPM components. We don't link the SRPM into the tree because some of it's RPMs
        # might not be included in the module
        if "brew_build_id" in c:
            slow_fetch_brew_build.delay(c["brew_build_id"])
        save_component(c, node)
    slow_fetch_brew_build.delay(build_id, force_process=force_process)
    logger.info("Finished fetching modular build: %s", build_id)


def save_component(
    component: dict, parent: ComponentNode, softwarebuild: Optional[SoftwareBuild] = None
):
    logger.debug("Called save component with component %s", component)
    component_type = component.pop("type")
    meta = component.get("meta", {})

    node_type = ComponentNode.ComponentNodeType.PROVIDES
    if meta.pop("dev", False):
        node_type = ComponentNode.ComponentNodeType.PROVIDES_DEV

    # Map Cachito (lowercase) type to Corgi TYPE, or use existing Corgi TYPE, or raise error
    if component_type in ("go-package", "gomod"):
        meta["go_component_type"] = component_type
        component_type = Component.Type.GOLANG
    elif component_type in Brew.CACHITO_PKG_TYPE_MAPPING:
        component_type = Brew.CACHITO_PKG_TYPE_MAPPING[component_type]
    elif component_type.upper() in Component.Type.values:
        component_type = component_type.upper()
    else:
        raise ValueError(f"Tried to create component with invalid component_type: {component_type}")

    related_url = meta.pop("url", "")
    if not related_url:
        # Only RPMs have a URL header, containers might have below
        related_url = meta.get("repository_url", "")
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    license_declared_raw = meta.pop("license", "")
    # We can't build a purl before the component is saved,
    # so we can't handle an IntegrityError (duplicate purl) here like we do in the SCA task
    # But that's OK - this task shouldn't ever raise an IntegrityError
    # The "original" component should be created here as part of normal ingestion
    # The duplicate components (new name, same purl) are created by Syft / the SCA task later
    obj, _ = Component.objects.update_or_create(
        type=component_type,
        name=meta.pop("name", ""),
        version=meta.pop("version", ""),
        release=meta.pop("release", ""),
        arch=meta.pop("arch", "noarch"),
        defaults={
            "description": meta.pop("description", ""),
            "namespace": Component.Namespace.REDHAT
            if component_type == Component.Type.RPM
            else Component.Namespace.UPSTREAM,
            "related_url": related_url,
            "epoch": int(meta.pop("epoch", 0)),
        },
    )

    set_license_declared_safely(obj, license_declared_raw)

    # Usually component_meta is an empty dict by the time we get here, but if it's not, and we have
    # new keys, add them to the existing meta_attr. Only call save if something has been added
    if meta:
        obj.meta_attr = obj.meta_attr | meta
        obj.save()

    node = save_node(node_type, parent, obj)
    recurse_components(component, node)


def save_srpm(softwarebuild: SoftwareBuild, build_data: dict) -> ComponentNode:
    name = build_data["meta"].pop("name")
    version = build_data["meta"].pop("version")
    related_url = build_data["meta"].pop("url", "")
    epoch = build_data["meta"].pop("epoch", 0)
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    extra = {
        "description": build_data["meta"].pop("description", ""),
        "license_declared_raw": build_data["meta"].pop("license", ""),
        "related_url": related_url,
    }

    obj, created = Component.objects.update_or_create(
        type=build_data["type"],
        name=name,
        version=version,
        release=build_data["meta"].pop("release", ""),
        arch=build_data["meta"].pop("arch", "noarch"),
        defaults={
            **extra,
            "meta_attr": build_data["meta"],
            "namespace": Component.Namespace.REDHAT,
            "software_build": softwarebuild,
            "epoch": int(epoch),
        },
    )
    node = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)
    if related_url:
        save_upstream(build_data["type"], name, version, build_data["meta"], extra, node)
    return node


def process_image_components(image):
    builds_to_fetch = set()
    if "rpm_components" in image:
        for rpm in image["rpm_components"]:
            builds_to_fetch.add(rpm["brew_build_id"])
        # TODO save the list of rpms by image to the container meta for reconcilation.
    return builds_to_fetch


def set_license_declared_safely(obj: Component, license_declared_raw: str) -> None:
    """Save a declared license onto a Component, without erasing any existing value"""
    if license_declared_raw and license_declared_raw != obj.license_declared_raw:
        # Any non-empty license here should be reported
        # We only rely on OpenLCS if we don't know the license_declared
        # But we can't set license_declared in update_or_create
        # If the license in the metadata is an empty string and we are reprocessing,
        # we might erase the license that OpenLCS provided
        # They cannot erase any licenses we set ourselves (when the field is not empty)
        # API endpoint blocks this (400 Bad Request)
        obj.license_declared_raw = license_declared_raw
        obj.save()


def save_container(softwarebuild: SoftwareBuild, build_data: dict) -> ComponentNode:
    license_declared_raw = build_data["meta"].pop("license", "")
    related_url = build_data["meta"].get("repository_url", "")
    if not related_url:
        # Handle case when key is present but value is None
        related_url = ""

    obj, created = Component.objects.update_or_create(
        type=build_data["type"],
        name=build_data["meta"].pop("name"),
        version=build_data["meta"].pop("version"),
        release=build_data["meta"].pop("release"),
        arch="noarch",
        defaults={
            "description": build_data["meta"].pop("description", ""),
            "filename": build_data["meta"].pop("filename", ""),
            "meta_attr": build_data["meta"],
            "namespace": Component.Namespace.REDHAT,
            "related_url": related_url,
            "software_build": softwarebuild,
        },
    )

    set_license_declared_safely(obj, license_declared_raw)
    root_node = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)

    if "upstream_go_modules" in build_data["meta"]:
        meta_attr = {"go_component_type": "gomod", "source": ["collectors/brew"]}
        for module in build_data["meta"]["upstream_go_modules"]:
            # the upstream commit is included in the dist-git commit history, but is not
            # exposed anywhere in the brew data that I can find, so can't set version
            save_upstream(Component.Type.GOLANG, module, "", meta_attr, {}, root_node)

    if "image_components" in build_data:
        for image in build_data["image_components"]:
            license_declared_raw = image["meta"].pop("license", "")

            obj, created = Component.objects.update_or_create(
                type=image["type"],
                name=image["meta"].pop("name"),
                version=image["meta"].pop("version"),
                release=image["meta"].pop("release"),
                arch=image["meta"].pop("arch"),
                defaults={
                    "description": image["meta"].pop("description", ""),
                    "filename": image["meta"].pop("filename", ""),
                    "meta_attr": image["meta"],
                    "namespace": Component.Namespace.REDHAT,
                },
            )

            set_license_declared_safely(obj, license_declared_raw)
            # Based on a conversation with the container factory team,
            # almost all image components are build-time dependencies in a multi-stage build
            # and are discarded / do not end up in the final image.
            # The only exceptions are image components from the base layer (ie UBI)
            # So we should probably still use PROVIDES here, and not PROVIDES_DEV
            # Unless we can distinguish between these two types of components
            # using some other Brew metadata
            image_arch_node = save_node(ComponentNode.ComponentNodeType.PROVIDES, root_node, obj)

            if "rpm_components" in image:
                for rpm in image["rpm_components"]:
                    save_component(rpm, image_arch_node)
                    # SRPMs are loaded using nested_builds

    if "sources" in build_data:
        for source in build_data["sources"]:
            component_name = source["meta"].pop("name")
            component_version = source["meta"].pop("version")
            related_url = source["meta"].pop("url", "")
            if not related_url:
                # Handle case when key is present but value is None
                related_url = ""
                if component_name.startswith("github.com/"):
                    related_url = f"https://{component_name}"
            if "openshift-priv" in related_url:
                # Component name is something like github.com/openshift-priv/cluster-api
                # The public repo we want is just github.com/openshift/cluster-api
                related_url = related_url.replace("openshift-priv", "openshift")

            if source["type"] == Component.Type.GOLANG:
                # Assume upstream container sources are always go modules, never go-packages
                source["meta"]["go_component_type"] = "gomod"

            extra = {"related_url": related_url}
            _, upstream_node = save_upstream(
                source["type"], component_name, component_version, source["meta"], extra, root_node
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
    obj, created = Component.objects.update_or_create(
        type=build_data["type"],
        name=build_data["meta"]["name"],
        version=build_data["meta"].get("version", ""),
        release=build_data["meta"].get("release", ""),
        arch=build_data["meta"].get("arch", "noarch"),
        defaults={
            "description": build_data["meta"].get("description", ""),
            "license_declared_raw": build_data["meta"].get("license", ""),
            "meta_attr": meta_attr,
            "namespace": Component.Namespace.REDHAT,
            "software_build": softwarebuild,
        },
    )
    node = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)

    return node


def save_upstream(
    component_type: str, name: str, version: str, meta_attr: dict, extra: dict, node: ComponentNode
) -> tuple[Component, ComponentNode]:
    """Helper function to save an upstream component and create a node for it"""
    upstream_component, _ = Component.objects.update_or_create(
        type=component_type,
        name=name,
        version=version,
        release="",
        arch="noarch",
        defaults={
            **extra,
            "meta_attr": meta_attr,
            "namespace": Component.Namespace.UPSTREAM,
        },
    )
    upstream_node = save_node(ComponentNode.ComponentNodeType.SOURCE, node, upstream_component)

    return upstream_component, upstream_node


def save_node(
    node_type: str, parent: Optional[ComponentNode], related_component: Component
) -> ComponentNode:
    """Helper function that wraps ComponentNode creation"""
    node, _ = ComponentNode.objects.get_or_create(
        type=node_type,
        parent=parent,
        purl=related_component.purl,
        defaults={"obj": related_component},
    )
    return node


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def load_stream_brew_tags() -> None:
    for ps in ProductStream.objects.get_queryset():
        brew, build_type, refresh_task = _relation_context_for_stream(ps.name)
        for brew_tag, inherit in ps.brew_tags.items():
            builds = brew.get_builds_with_tag(brew_tag, inherit=inherit, latest=False)
            no_of_created = create_relations(
                builds,
                build_type,
                brew_tag,
                ps.name,
                ProductComponentRelation.Type.BREW_TAG,
                refresh_task,
            )
            logger.info("Saving %s new builds for %s", no_of_created, brew_tag)


def load_brew_tags(software_build: SoftwareBuild, brew_tags: list[str]) -> int:
    all_stream_tags = ProductStream.objects.exclude(brew_tags__exact={}).values_list(
        "name", "brew_tags"
    )
    no_created = 0
    distinct_brew_tags = set(brew_tags)
    for stream_name, stream_tags in all_stream_tags:
        distinct_stream_tags = set(stream_tags)
        distinct_stream_tags = distinct_brew_tags.intersection(distinct_stream_tags)
        for tag in distinct_stream_tags:
            brew, build_type, _ = _relation_context_for_stream(stream_name)
            logger.info(f"Creating relations for {stream_name} and {tag}")
            # We do this inline instead of via create_relations function because
            # it's the only time we call it where we have a software_build object created
            _, created = ProductComponentRelation.objects.update_or_create(
                external_system_id=tag,
                product_ref=stream_name,
                build_id=software_build.build_id,
                build_type=build_type,
                defaults={
                    "type": ProductComponentRelation.Type.BREW_TAG,
                    "software_build": software_build,
                },
            )
            if created:
                no_created += 1
    return no_created


def _relation_context_for_stream(stream_name: str):
    build_type = BUILD_TYPE
    brew = Brew(BUILD_TYPE)
    refresh_task = slow_fetch_modular_build
    # This should really be a property in Product Definitions
    if settings.COMMUNITY_MODE_ENABLED and stream_name == "openstack-rdo":
        brew = Brew(SoftwareBuild.Type.CENTOS)
        build_type = SoftwareBuild.Type.CENTOS
        refresh_task = slow_fetch_brew_build
    return brew, build_type, refresh_task


def fetch_modular_builds(relations_query: QuerySet, force_process: bool = False) -> None:
    for build_id in relations_query:
        slow_fetch_modular_build.delay(build_id, force_process=force_process)


def fetch_unprocessed_relations(
    created_since: Optional[datetime] = None,
    product_ref: Optional[str] = "",
    relation_type: Optional[ProductComponentRelation.Type] = None,
    force_process: bool = False,
) -> int:
    """Load Brew builds for relations which don't have an associated SoftwareBuild.
    Accepts optional arguments product_ref and relation_type which add query filters"""
    query = Q()
    if relation_type:
        query &= Q(type=relation_type)
        logger.info(f"Processing relations of type {relation_type}")
    if product_ref:
        query &= Q(product_ref=product_ref)
        logger.info(f"Processing relations with product reference {product_ref}")

    if created_since:
        query &= Q(created_at__gte=created_since)
    relations_query = ProductComponentRelation.objects.filter(query).filter(software_build=None)

    processed_builds = 0
    for relation in relations_query.iterator():
        logger.info(f"Processing {relation.type} relation build with id: {relation.build_id}")
        if relation.build_type == SoftwareBuild.Type.CENTOS:
            # This skips use of the Collector models for builds in the CENTOS koji instance
            # It was done to avoid updating the collector models not to use build_id as
            # a primary key. It's possible because the only product stream (openstack-rdo)
            # stored in CENTOS koji doesn't use modules
            slow_fetch_brew_build.delay(
                relation.build_id, SoftwareBuild.Type.CENTOS, force_process=force_process
            )
        else:
            slow_fetch_modular_build.delay(relation.build_id, force_process=force_process)
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
        created_dt = timezone.now() - timedelta(days=days_created_since)
    else:
        created_dt = get_last_success_for_task(
            "corgi.tasks.brew.fetch_unprocessed_brew_tag_relations"
        )
    return fetch_unprocessed_relations(
        relation_type=ProductComponentRelation.Type.BREW_TAG,
        force_process=force_process,
        created_since=created_dt,
    )


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_update_brew_tags(build_id: str, tag_added: str = "", tag_removed: str = "") -> str:
    """Update a build's list of tags in Corgi when they change in Brew"""
    if not tag_added and not tag_removed:
        raise ValueError("Must supply one tag to be added or removed")

    with transaction.atomic():
        build = SoftwareBuild.objects.filter(
            build_id=build_id, build_type=SoftwareBuild.Type.BREW
        ).first()
        if not build:
            logger.warning(f"Brew build with matching ID not ingested (yet?): {build_id}")
            # Include warning message in Celery task result
            # We don't raise an error here because there are potentially many builds
            # that we haven't loaded, but which could have tags updated at any time
            return f"Brew build with matching ID not ingested (yet?): {build_id}"

        if tag_added:
            tags = set(build.meta_attr["tags"])
            tags.add(tag_added)
            build.meta_attr["tags"] = sorted(tags)
            errata_tag = ADVISORY_REGEX.match(tag_added)
            if errata_tag:
                # Below should automatically create new relations for this build / erratum
                slow_load_errata.delay(errata_tag.group())
        else:
            try:
                build.meta_attr["tags"].remove(tag_removed)
                # TODO: Clean up old relations for some build / erratum when a tag is removed
            except ValueError:
                # Tag to be removed not found in list
                # i.e. it was renamed earlier and is now being removed
                # We don't get a UMB event for these renames
                # Refresh all the tags so we have the most current data
                logger.warning(f"Tag to remove {tag_removed} not found, so refreshing all tags")
                slow_refresh_brew_build_tags.delay(int(build_id))
                return f"Tag to remove {tag_removed} not found, so refreshing all tags"

        build.meta_attr["errata_tags"] = Brew.extract_advisory_ids(build.meta_attr["tags"])
        build.meta_attr["released_errata_tags"] = Brew.parse_advisory_ids(
            build.meta_attr["errata_tags"]
        )
        build.save()
        return f"Added tag {tag_added} or removed tag {tag_removed} for build {build_id}"


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_refresh_brew_build_tags(build_id: int) -> None:
    """Refresh tags for a Brew build when some erratum releases it"""
    # We can't rely on above tag added / removed logic
    # The tags are only renamed, but there's no UMB event for this
    # Errata Tool's UMB messages only have info about the errata tags
    # We also need to update non-errata tags that link streams to builds
    # e.g. stream-name-candidate will change to stream-name-released

    logger.info(f"Refreshing Brew build tags for {build_id}")
    brew = Brew(SoftwareBuild.Type.BREW)
    tags = sorted(set(tag["name"] for tag in brew.koji_session.listTags(build_id)))
    errata_tags = Brew.extract_advisory_ids(tags)
    released_errata_tags = Brew.parse_advisory_ids(errata_tags)

    with transaction.atomic():
        # Can't use .update(key="value") on individual keys in a JSONField
        build = SoftwareBuild.objects.get(
            build_type=SoftwareBuild.Type.BREW, build_id=str(build_id)
        )
        # If the newly-refreshed tags have errata that weren't present before
        # We need to create relations for these new errata tags
        new_errata_tags = set(errata_tags) - set(build.meta_attr["errata_tags"])

        build.meta_attr["tags"] = tags
        build.meta_attr["errata_tags"] = errata_tags
        build.meta_attr["released_errata_tags"] = released_errata_tags
        build.save()

    for erratum_id in sorted(new_errata_tags):
        slow_load_errata.delay(erratum_id)
    logger.info(f"Finished refreshing Brew build tags for {build_id}")


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_delete_brew_build(build_id: int, build_state: int) -> int:
    """Delete a Brew build (and its relations) in Corgi when it's deleted in Brew"""
    # Shipped builds (which have a Brew tag for any product) are never deleted
    # Only unshipped builds not part of any product are deleted
    # For example, builds that failed QE will not become part of a product
    # We don't need this data for any of our use cases
    # Keeping it causes other issues when reloading old builds, scanning for licenses, etc.

    logger.info(f"Deleting Brew build {build_id} with state {build_state}")
    if build_state != koji.BUILD_STATES["DELETED"]:
        raise ValueError(
            f"Invalid state for build {build_id}: "
            f"expected {koji.BUILD_STATES['DELETED']}, received {build_state}"
        )

    deleted_count = 0
    with transaction.atomic():
        # Get a root component's PK, if possible
        # Build might not exist, or might exist but have no root components (?)
        # Should only be 1 result if any
        root_component_qs = (
            SoftwareBuild.objects.filter(build_id=build_id, build_type=SoftwareBuild.Type.BREW)
            .exclude(components__isnull=True)
            .values_list("components", "pk")
        )
        if len(root_component_qs) > 1:
            raise ValueError(
                f"Brew build {build_id} had multiple root components: {root_component_qs}"
            )

        root_component_and_build_pks = root_component_qs.first()
        # Skip deleting child components when build doesn't exist
        if root_component_and_build_pks:
            root_component_pk, build_pk = root_component_and_build_pks
            # The build has a root component, so delete the child components that are
            # provided by / upstreams of only this build's root, and not any other builds
            # Deleting the Component will automatically delete the ComponentNodes
            provided_components = Component.objects.annotate(
                build_count=Count("sources__software_build_id")
            ).filter(sources=root_component_pk, build_count=1)
            deleted_count += _delete_queryset(provided_components, "provided component")

            # Doing .filter(downstreams=root_component_pk)
            # before .annotate(downstreams_count=Count("downstreams"))
            # makes Django return the wrong results
            # https://docs.djangoproject.com/en/3.2/topics/db/aggregation/
            # #order-of-annotate-and-filter-clauses
            upstream_components = Component.objects.annotate(
                build_count=Count("downstreams__software_build_id")
            ).filter(downstreams=root_component_pk, build_count=1)
            deleted_count += _delete_queryset(upstream_components, "upstream component")

        # Relations without a linked (NULL) build are "unprocessed"
        # We process these relations each day by loading their build ID
        # Deleting the relation avoids reloading the build we are deleting
        relations = ProductComponentRelation.objects.filter(
            build_id=build_id, build_type=SoftwareBuild.Type.BREW
        )
        deleted_count += _delete_queryset(relations, "relation")

        # Deleting the build automatically deletes the linked root component
        # But not the child components, so we handled those separately above
        builds = SoftwareBuild.objects.filter(build_id=build_id, build_type=SoftwareBuild.Type.BREW)
        deleted_count += _delete_queryset(builds, "build")

    return deleted_count


def _delete_queryset(queryset: QuerySet, model_name: str) -> int:
    """Delete all rows in some queryset, then parse and return the number of deleted objects
    (including both direct and CASCADE / transitive deletions due to ForeignKeys)"""
    # When we call .delete() on a queryset, Django returns a tuple with 2 elements
    # The first element is the total number of models we deleted
    # The second element is a dict. The keys are this model's name, plus all the related model names
    # which have a ForeignKey to the deleted model that defines on_delete=models.CASCADE
    # The values are the number of model instances we deleted either directly or due to a ForeignKey
    # Note that for ManyToManyFields, the counts / related models include entries
    # for the "through tables" that are used to map e.g. many provided components to many sources
    deleted_info: tuple[int, dict[str, int]] = queryset.delete()
    deleted_count, deleted_links = deleted_info
    logger.info(f"Deleted {deleted_count} {model_name}(s) and related models: {deleted_links}")
    return deleted_count

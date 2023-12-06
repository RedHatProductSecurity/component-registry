from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from requests import HTTPError

from config.celery import app
from corgi.collectors.app_interface import AppInterface
from corgi.collectors.syft import GitCloneError, QuayImagePullError, Syft
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, slow_save_taxonomy
from corgi.tasks.sca import save_component

logger = get_task_logger(__name__)


class MultiplePermissionExceptions(Exception):
    """Helper class that tracks messages for multiple exceptions
    so that we can at least attempt to scan all images / repos
    and raise any permission errors for private images / repos at the end
    instead of stopping on the first failure"""

    def __init__(self, error_messages: list[str]) -> None:
        combined_message = "\n".join(message for message in error_messages)
        combined_message = f"Multiple exceptions raised:\n\n{combined_message}"
        super().__init__(combined_message)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def refresh_service_manifests() -> None:
    """Collect data for all Red Hat managed services from product-definitions
    and App Interface, then remove and recreate all services"""
    services = ProductStream.objects.filter(
        meta_attr__managed_service_components__isnull=False
    ).distinct()
    service_metadata = AppInterface.fetch_service_metadata(services)

    # Delete all APP_INTERFACE relations / builds in advance, before we start creating anything.
    # Services can reuse components, so this avoids deleting a build / component for one service
    # while trying to create the same build / component for another service in another task.
    # This also cleans up any old builds / components which were removed from product-definitions.
    # We should recreate builds / components that are still present in prod-defs
    # when we process the list of components for that service below.
    ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.APP_INTERFACE
    ).delete()

    # This technically suffers from race conditions
    # when the last 2 tasks to remove data run at the same time
    # as the first task to add new data (when there are 3 workers)
    # Processing by service names or build / component names
    # or running deletion inside a transaction is more likely to have bugs

    # Preventing this would be too complex, and actual bugs should be rare
    # since the last builds we remove are the newest, due to order_by()
    # and the first builds / service components we recreate are the oldest
    # So data we're deleting shouldn't normally be reused by data we're recreating

    # Bugs may happen if the last builds / components we remove (or their dependencies)
    # start being used for the first time, by the first service we recreate
    # This should fix itself when data is recreated the next day
    for build_id in (
        SoftwareBuild.objects.filter(build_type=SoftwareBuild.Type.APP_INTERFACE)
        .values_list("build_id", flat=True)
        .order_by("build_id")
    ):
        cpu_remove_old_services_data.delay(build_id)

    for service, components in service_metadata.items():
        cpu_manifest_service.delay(service.name, components)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_remove_old_services_data(build_id: int) -> None:
    """Find previous builds and delete them and their components,
    so we can create a fresh manifest structure for each "new" build."""
    # This is done specifically because we don't have a way
    # to tie a set of components to a specific build right now,
    # so we construct an arbitrary build periodically even if nothing has changed since
    # the last time we looked. Historical data is not needed as of right now.
    build = SoftwareBuild.objects.get(
        build_id=build_id, build_type=SoftwareBuild.Type.APP_INTERFACE
    )
    for root_component in build.components.get_queryset():
        children_to_delete = set()

        # Delete child components, if they're only provided by the root we're about to delete
        for provided_component in root_component.provides.get_queryset():
            if provided_component.cnodes.count() == 1:
                children_to_delete.add(provided_component.pk)

        # Do this in two steps to reduce size of set / overall memory requirements
        Component.objects.filter(pk__in=children_to_delete).delete()
        children_to_delete = set()

        # Delete child components, if they're only upstream of the root we're about to delete
        for upstream_component in root_component.upstreams.get_queryset():
            if upstream_component.cnodes.count() == 1:
                children_to_delete.add(upstream_component.pk)

        # Nodes will be automatically deleted when their linked component is deleted
        Component.objects.filter(pk__in=children_to_delete).delete()

    # Root components will be automatically deleted when their linked build is deleted
    build.delete()


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_manifest_service(stream_name: str, service_components: list[dict[str, str]]) -> None:
    """Analyze components for some Red Hat managed service
    based on data from product-definitions and App Interface"""
    logger.info(f"Manifesting service {stream_name}")
    service = ProductStream.objects.get(name=stream_name)
    exceptions = []

    for service_component in service_components:
        logger.info(f"Processing component for service {stream_name}: {service_component}")
        now = timezone.now()
        analyzed_components = []
        component_version = ""

        quay_repo = service_component.get("quay_repo_name")
        if quay_repo:
            logger.info(f"Scanning Quay repo {quay_repo} for service {stream_name}")
            try:
                component_data, quay_scan_source = Syft.scan_repo_image(target_image=quay_repo)
                component_version = quay_scan_source["target"]["imageID"]
                analyzed_components.extend(component_data)
            except (QuayImagePullError, HTTPError) as e:
                # We want to raise other (unexpected) errors
                # Only ignore if it's an (expected) pull failure for private images
                if isinstance(e, HTTPError) and e.response.status_code != 403:
                    raise e
                # or an (expected) pull failure for images with no tags
                # don't hide other network-related HTTPErrors or Subprocess exceptions
                # Don't continue here in case component also defines a git repo
                # Which we still need to / haven't checked yet (below)
                exceptions.append(f"{type(e)}: {e.args}\n")

        git_repo = service_component.get("git_repo_url")
        if git_repo:
            logger.info(f"Scanning Git repo {git_repo} for service {stream_name}")
            try:
                component_data, source_ref = Syft.scan_git_repo(target_url=git_repo)
                if not component_version:
                    component_version = source_ref
                analyzed_components.extend(component_data)
            except GitCloneError as e:
                # We want to raise other (unexpected) errors
                # Only ignore if it's an (expected) clone failure for private repos
                # Don't continue here in case component also defined a Quay image above
                # Which we still need to / haven't saved yet (below)
                exceptions.append(f"{type(e)}: {e.args}\n")

        if not analyzed_components:
            if not quay_repo and not git_repo:
                raise ValueError(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"didn't define either a Quay repo or Git repo URL"
                )
            logger.warning(
                f"Service component {service_component['name']} for service {stream_name} "
                "didn't have any child components after analyzing "
                f"its Quay ({quay_repo}) and / or Git ({git_repo}) repos"
            )
            continue

        with transaction.atomic():
            build_id = now.strftime("%Y%m%d%H%M%S%f")
            try:
                # get_or_create requires all keyword arguments to appear in
                # a unique_together constraint, like build_id and build_type
                # Otherwise it will not be atomic and can behave incorrectly
                # Since we don't have a database index for name + build_type,
                # we try to find an APP_INTERFACE build with a matching name first
                # And update the list of services it belongs to
                # If that fails, we'll create a new build
                # So Corgi reuses a component when services reuse the component
                build = SoftwareBuild.objects.get(
                    name=service_component["name"],
                    build_type=SoftwareBuild.Type.APP_INTERFACE,
                )
                build.meta_attr["services"].append(service.name)
                build.save()
                logger.info(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"had an existing {build.build_type} build with ID {build_id}, "
                    f"and is used by multiple services: {build.meta_attr['services']}"
                )

            except SoftwareBuild.DoesNotExist:
                build = SoftwareBuild.objects.create(
                    name=service_component["name"],
                    build_type=SoftwareBuild.Type.APP_INTERFACE,
                    build_id=build_id,
                    completion_time=now,
                    meta_attr={"services": [service.name]},
                )
                logger.info(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"created a new {build.build_type} build with ID {build_id}"
                )

            # Root components can only be linked to one build
            # if we use the same build / component for two different services,
            # we will analyze the same component twice
            # this should be OK and the taxonomy should be the same.
            # Or if not, merging the data is probably what we want.

            # We can't use namespace in Component get_or_create / update_or_create kwargs
            # We can only use name, version, etc. fields that are part of the NEVRA
            # We don't want to get an existing component in the UPSTREAM namespace
            # or update an existing component to use the REDHAT namespace
            # so we do a get with the correct namespace first
            # then create a new component with the correct namespace only if the get fails
            # This is expected to fail, for safety, if some GitHub repo already exists
            # but uses the UPSTREAM namespace instead of the REDHAT namespace
            root_component_kwargs = {
                "type": Component.Type.CONTAINER_IMAGE if quay_repo else Component.Type.GITHUB,
                "name": service_component["name"],
                "version": component_version,
                "release": "",
                "arch": "noarch",
                "namespace": Component.Namespace.REDHAT,
                "software_build": build,
            }
            try:
                root_component = Component.objects.get(**root_component_kwargs)
                logger.info(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"had an existing root component with purl {root_component.purl}"
                )
            except Component.DoesNotExist:
                root_component = Component.objects.create(**root_component_kwargs)
                logger.info(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"created a new root component with purl {root_component.purl}"
                )

            # the index uses type / parent / purl for lookups
            root_node, _ = ComponentNode.objects.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=None,
                purl=root_component.purl,
                defaults={
                    "obj": root_component,
                },
            )

            logger.info(
                f"Service component {service_component['name']} for service {stream_name} "
                f"has {len(analyzed_components)} child components"
            )
            created_count = 0
            for component in analyzed_components:
                logger.debug(
                    f"Service component {service_component['name']} for service {stream_name} "
                    f"had a child component {component}"
                )
                obj_or_node_created = save_component(component, root_node)
                if obj_or_node_created:
                    created_count += 1

            logger.info(
                f"service component {service_component['name']} for service {stream_name} "
                f"had {created_count} new child components created, now saving relation / taxonomy"
            )
            ProductComponentRelation.objects.create(
                product_ref=service.name,
                build_id=build_id,
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                software_build=build,
                type=ProductComponentRelation.Type.APP_INTERFACE,
            )

        # Give the transaction time to commit, before looking up the build we just created
        slow_save_taxonomy.apply_async(args=(build.build_id, build.build_type), countdown=10)

        logger.info(
            f"Finished processing service component {service_component['name']} "
            f"for service {stream_name}"
        )
    if exceptions:
        # Now we're finished processing all the components for this service
        # including both Quay images and Git repos for each component
        # we can safely raise errors here for private images / repos
        # so there will be no gaps in the service's list of components
        # and we'll still get alerted about components with missing permissions
        raise MultiplePermissionExceptions(exceptions)
    logger.info(f"Finished manifesting service {stream_name}")

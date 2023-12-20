from datetime import datetime
from subprocess import CalledProcessError

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from requests import HTTPError

from config.celery import app
from corgi.collectors.app_interface import AppInterface
from corgi.collectors.syft import GitCloneError, Syft
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

    def __init__(self, error_messages: tuple[str, ...]) -> None:
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
    services = tuple(
        ProductStream.objects.filter(meta_attr__managed_service_components__isnull=False)
        .values_list("name", "meta_attr__managed_service_components")
        .distinct("name")
    )
    service_metadata = AppInterface.fetch_service_metadata(services)

    # Manifest individually to avoid errors for individual components
    # and timeouts for large services with many components
    # that block analyzing the remaining components for the service
    for component in service_metadata.values():
        service_names = component.pop("services")
        cpu_manifest_service_component.delay(list(service_names), component)

    # Clean up any old builds / components which are still present in Corgi
    # but were removed from product-definitions.
    for build_id in (
        SoftwareBuild.objects.filter(build_type=SoftwareBuild.Type.APP_INTERFACE)
        .exclude(name__in=service_metadata.keys())
        .values_list("build_id", flat=True)
        .order_by("build_id")
    ):
        cpu_remove_old_services_data.delay(build_id)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_remove_old_services_data(build_id: int) -> None:
    """Find previous managed service builds and delete them,
    after unlinking their components from each related services stream,
    so we don't leave behind stale data / VM doesn't file trackers for already-fixed CVEs."""
    # This is done specifically because we don't have a way
    # to tie a set of components to a specific build right now,
    # so we construct an arbitrary build periodically even if nothing has changed since
    # the last time we looked. Historical data is not needed as of right now.
    build = SoftwareBuild.objects.get(
        build_id=build_id, build_type=SoftwareBuild.Type.APP_INTERFACE
    )
    for root_component in build.components.get_queryset():
        # TODO: Clean up deletion logic per notes
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

    ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.APP_INTERFACE, build_id=build_id
    ).delete()

    old_services = build.meta_attr["services"]
    logger.info(
        f"App-Interface build {build_id} and all its current children "
        f"will be unlinked from old services that no longer use it: {old_services}"
    )
    old_stream_pks = ProductStream.objects.filter(name__in=old_services).values_list(
        "pk", flat=True
    )
    build.disassociate_with_product("ProductStream", old_stream_pks)
    # Root components will be automatically deleted when their linked build is deleted
    # This also deletes all the nodes which are children of the root (the entire tree)
    build.delete()


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_manifest_service_component(
    stream_names: list[str], service_component: dict[str, str]
) -> None:
    """Analyze a single component used by some Red Hat managed service(s)
    based on data from product-definitions and App Interface"""
    logger.info(f"Processing component used by services {stream_names}: {service_component}")
    # Make sure the service exists, although we only need the name
    for stream_name in stream_names:
        ProductStream.objects.get(name=stream_name)
    exceptions: list[CalledProcessError | GitCloneError | HTTPError] = []

    now = timezone.now()
    analyzed_components = []
    component_version = ""

    quay_repo = service_component.get("quay_repo_name")
    if quay_repo:
        logger.info(f"Scanning Quay repo {quay_repo} used by services {stream_names}")
        try:
            component_data, quay_scan_source = Syft.scan_repo_image(target_image=quay_repo)
            component_version = quay_scan_source["target"]["imageID"]
            analyzed_components.extend(component_data)
        except (CalledProcessError, HTTPError) as e:
            # We want to raise other (unexpected) errors
            # Only ignore if it's an (expected) pull failure for private images
            if isinstance(e, HTTPError) and e.response.status_code != 403:
                raise e
            # Don't return here in case component also defines a git repo
            # Which we still need to / haven't checked yet (below)
            exceptions.append(e)

    git_repo = service_component.get("git_repo_url")
    if git_repo:
        logger.info(f"Scanning Git repo {git_repo} used by services {stream_names}")
        try:
            component_data, source_ref = Syft.scan_git_repo(target_url=git_repo)
            if not component_version:
                component_version = source_ref
            analyzed_components.extend(component_data)
        except GitCloneError as e:
            # We want to raise other (unexpected) errors
            # Only ignore if it's an (expected) clone failure for private repos
            # Don't return here in case component also defined a Quay image above
            # Which we still need to / haven't saved yet (below)
            exceptions.append(e)

    if not analyzed_components:
        if not quay_repo and not git_repo:
            raise ValueError(
                f"Service component {service_component['name']} used by services {stream_names} "
                f"didn't define either a Quay repo or Git repo URL"
            )
        logger.error(
            f"Service component {service_component['name']} used by services {stream_names} "
            "didn't have any child components after analyzing "
            f"its Quay ({quay_repo}) and / or Git ({git_repo}) repos"
        )
        # Raise exceptions here if we failed to analyze a repo / image
        if exceptions:
            raise_multiple_exceptions(exceptions)
        # If no exceptions, we still create the build and relations below,
        # as well as save the taxonomy, even if there are no components
        # just so that this root component is properly linked to the service

    save_service_components(
        now,
        service_component,
        stream_names,
        exceptions,
        Component.Type.CONTAINER_IMAGE if quay_repo else Component.Type.GITHUB,
        component_version,
        analyzed_components,
    )


def save_service_components(
    now: datetime,
    service_component: dict[str, str],
    stream_names: list[str],
    exceptions: list[CalledProcessError | GitCloneError | HTTPError],
    component_type: Component.Type,
    component_version: str,
    analyzed_components: list[dict],
):
    """Helper method to save the components we discovered above"""
    with transaction.atomic():
        build_id = now.strftime("%Y%m%d%H%M%S%f")
        build_kwargs = {
            "name": service_component["name"],
            "build_type": SoftwareBuild.Type.APP_INTERFACE,
            "build_id": build_id,
            "completion_time": now,
            "meta_attr": {"services": stream_names},
        }

        try:
            # get_or_create requires all keyword arguments to appear in
            # a unique_together constraint, like build_id and build_type
            # Otherwise it will not be atomic and can behave incorrectly
            # Since we don't have a database index for name + build_type,
            # we try to find an APP_INTERFACE build with a matching name first
            # And update the list of services it belongs to
            # If that fails, we'll create a new build
            build = SoftwareBuild.objects.get(
                name=service_component["name"],
                build_type=SoftwareBuild.Type.APP_INTERFACE,
            )
            old_services = set(build.meta_attr["services"])
            logger.info(
                f"Service component {service_component['name']} "
                f"had an existing {build.build_type} build with ID {build_id}, "
                f"was used by multiple old services: {old_services}, "
                f"and is now used by multiple new services: {stream_names}"
            )

            # We unlink this root component and all its children from all older services
            # so that VM does not see stale data / file bad trackers
            # after a service stops using some root component
            logger.info(
                f"Service component {service_component['name']} and all its current children "
                f"will be unlinked from old services that no longer use it: {old_services}"
            )
            old_stream_pks = ProductStream.objects.filter(name__in=old_services).values_list(
                "pk", flat=True
            )
            build.disassociate_with_product("ProductStream", old_stream_pks)

            # We delete and recreate this build, root component, and all nodes
            # so that VM does not see stale data / file bad trackers
            # after a root component upgrades to a newer version of a child component
            # TODO: Stale provided / upstream child components will be orphaned and not deleted
            #  if the old service was the only stream that used them, but is no longer linked
            #  or the current service no longer depends on that version
            #  The orphaned components are no longer linked,
            #  so at least no one will see bad search results - we just waste a little DB space
            build.delete()

        except SoftwareBuild.DoesNotExist:
            pass

        build = SoftwareBuild.objects.create(**build_kwargs)
        logger.info(
            f"Service component {service_component['name']} used by services {stream_names} "
            f"created a new {build.build_type} build with ID {build_id}"
        )

        # We must delete and recreate at least the build / root component to avoid stale data
        # This is expected to fail, for safety, if some e.g. GitHub repo already exists
        # but uses the UPSTREAM namespace instead of the REDHAT namespace
        root_component_kwargs = {
            "type": component_type,
            "name": service_component["name"],
            "version": component_version,
            "release": "",
            "arch": "noarch",
            "namespace": Component.Namespace.REDHAT,
            "software_build": build,
        }
        root_component = Component.objects.create(**root_component_kwargs)
        logger.info(
            f"Service component {service_component['name']} used by services {stream_names} "
            f"created a new root component with purl {root_component.purl}"
        )
        root_node = ComponentNode.objects.create(
            type=ComponentNode.ComponentNodeType.SOURCE,
            parent=None,
            obj=root_component,
        )

        logger.info(
            f"Service component {service_component['name']} used by services {stream_names} "
            f"has {len(analyzed_components)} child components"
        )
        created_count = 0
        for component in analyzed_components:
            logger.debug(
                f"Service component {service_component['name']} used by services {stream_names} "
                f"had a child component {component}"
            )
            obj_or_node_created = save_component(component, root_node)
            if obj_or_node_created:
                created_count += 1

        logger.info(
            f"service component {service_component['name']} used by services {stream_names} "
            f"had {created_count} new child components created, now saving relations / taxonomy"
        )
        for stream_name in stream_names:
            ProductComponentRelation.objects.create(
                product_ref=stream_name,
                build_id=build_id,
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                software_build=build,
                type=ProductComponentRelation.Type.APP_INTERFACE,
            )

    # Give the transaction time to commit, before looking up the build we just created
    slow_save_taxonomy.apply_async(args=(build.build_id, build.build_type), countdown=10)

    logger.info(
        f"Finished processing service component {service_component['name']} "
        f"for services {stream_names}"
    )
    if exceptions:
        # Now we're finished processing both Quay images
        # and Git repos for this component
        # we can safely raise errors here for private images / repos
        # so there will be no gaps in the service's list of components
        # and we'll still get alerted about components with missing permissions
        raise_multiple_exceptions(exceptions)


def raise_multiple_exceptions(
    exceptions: list[CalledProcessError | GitCloneError | HTTPError],
) -> None:
    """Helper method to parse multiple exceptions with known types,
    combine their error messages and arguments, and raise a single exception for all of them"""
    exception_strings = tuple(f"{type(exc)}: {exc.args }\n" for exc in exceptions)
    raise MultiplePermissionExceptions(exception_strings)

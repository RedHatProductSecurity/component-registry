from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from config.celery import app
from corgi.collectors.app_interface import AppInterface
from corgi.collectors.syft import Syft
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    ProductStream,
    SoftwareBuild,
)
from corgi.tasks.brew import slow_save_taxonomy
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.sca import save_component


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def refresh_service_manifests() -> None:
    services = ProductStream.objects.filter(meta_attr__managed_service_components__isnull=False)
    service_metadata = AppInterface.fetch_service_metadata(list(services))

    # Find previous builds and delete them and their components, so we can create a fresh
    # manifest structure for each "new" build. This is done specifically because we don't
    # have a way to tie a set of components to a specific build right now,
    # so we construct an arbitrary build periodically even if nothing has changed since
    # the last time we looked. Historical data is not needed as of right now.

    # Delete all APP_INTERFACE builds in advance, before we start creating anything.
    # Services can reuse components, so this avoids deleting a build / component for one service
    # while trying to create the same build / component for another service in another task.
    # This also cleans up any old builds / components which were removed from product-definitions.
    # We should recreate builds / components that are still present in prod-defs
    # when we process the list of components for that service below.
    SoftwareBuild.objects.filter(build_type=SoftwareBuild.Type.APP_INTERFACE).delete()
    ProductComponentRelation.objects.filter(
        type=ProductComponentRelation.Type.APP_INTERFACE
    ).delete()
    # TODO: Deleting the build deletes the linked root component
    #  but what about the root component's child (provided / upstream) components?
    #  Check cleanup logic

    for service, components in service_metadata.items():
        cpu_manifest_service.delay(str(service.pk), components)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_manifest_service(product_stream_id: str, service_components: list) -> None:
    service = ProductStream.objects.get(pk=product_stream_id)

    for service_component in service_components:
        now = timezone.now()
        analyzed_components = []
        component_version = ""

        quay_repo = service_component.get("quay_repo_name")
        if quay_repo:
            quay_repo_full = f"quay.io/{quay_repo}"
            component_data, quay_scan_source = Syft.scan_repo_image(target_image=quay_repo_full)
            component_version = quay_scan_source["target"]["imageID"]
            analyzed_components.extend(component_data)

        git_repo = service_component.get("git_repo_url")
        if git_repo:
            component_data, source_ref = Syft.scan_git_repo(target_url=git_repo)
            if not component_version:
                component_version = source_ref
            analyzed_components.extend(component_data)

        if not analyzed_components:
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
            except SoftwareBuild.DoesNotExist:
                build = SoftwareBuild.objects.create(
                    name=service_component["name"],
                    build_type=SoftwareBuild.Type.APP_INTERFACE,
                    build_id=build_id,
                    completion_time=now,
                    meta_attr={"services": [service.name]},
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
            except Component.DoesNotExist:
                root_component = Component.objects.create(**root_component_kwargs)

            # the index uses type / parent / purl for lookups
            root_node, _ = ComponentNode.objects.get_or_create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=None,
                purl=root_component.purl,
                defaults={
                    "obj": root_component,
                },
            )

            for component in analyzed_components:
                if "-" in component["meta"]["version"]:
                    # Syft uses version-release as the version
                    # And then get_or_create fails (different NEVRA, same purl)
                    # release needs to be in its own field to match other components
                    version, release = component["meta"]["version"].split("-", 1)
                    component["meta"]["version"] = version
                    component["meta"]["release"] = release
                save_component(component, root_node)

            ProductComponentRelation.objects.create(
                product_ref=service.name,
                build_id=build_id,
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                software_build=build,
                type=ProductComponentRelation.Type.APP_INTERFACE,
            )

            slow_save_taxonomy.delay(build.build_id, build.build_type)

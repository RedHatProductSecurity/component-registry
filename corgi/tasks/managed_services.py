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
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.sca import save_component


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def refresh_service_manifests() -> None:
    services = ProductStream.objects.filter(meta_attr__managed_service_components__isnull=False)
    service_metadata = AppInterface.fetch_service_metadata(list(services))

    for service, components in service_metadata.items():
        cpu_manifest_service.delay(str(service.pk), components)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_manifest_service(product_stream_id: str, service_components: list):
    service = ProductStream.objects.get(pk=product_stream_id)

    now = timezone.now()
    for service_component in service_components:
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
            # Find previous build and delete it and its components, so we can create a fresh
            # manifest structure for this "new" build. This is done specifically because we don't
            # have a way to tie a set of components to a specific build right now,
            # so we construct an arbitrary build periodically even if nothing has changed since
            # the last time we looked. Historical data is not needed as of right now.
            past_builds = SoftwareBuild.objects.filter(
                name=service_component["name"],
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                meta_attr__service=service.name,
            )
            for build in past_builds:
                ProductComponentRelation.objects.filter(software_build=build).delete()
                build.delete()

            build_id = now.strftime("%Y%m%d%H%M%S")
            build = SoftwareBuild.objects.create(
                name=service_component["name"],
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                build_id=build_id,
                completion_time=now,
                meta_attr={"service": service.name},
            )

            root_component = Component.objects.create(
                type=Component.Type.CONTAINER_IMAGE if quay_repo else Component.Type.GITHUB,
                name=service_component["name"],
                version=component_version,
                release="",
                arch="noarch",
                namespace=Component.Namespace.REDHAT,
                software_build=build,
            )
            root_node = ComponentNode.objects.create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=None,
                purl=root_component.purl,
                obj=root_component,
            )

            for component in analyzed_components:
                save_component(component, root_node)

            ProductComponentRelation.objects.create(
                product_ref=service.name,
                build_id=build_id,
                build_type=SoftwareBuild.Type.APP_INTERFACE,
                software_build=build,
                type=ProductComponentRelation.Type.APP_INTERFACE,
            )

            build.save_product_taxonomy()
            for component_obj in build.components.get_queryset().iterator():
                component_obj.save_component_taxonomy()

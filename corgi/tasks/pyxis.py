import urllib.parse
from collections.abc import Mapping
from typing import Any

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.utils import dateparse
from django.utils.timezone import make_aware

from config.celery import app
from corgi.collectors.pyxis import get_manifest_data
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    SoftwareBuild,
)
from corgi.tasks.brew import save_node, set_license_declared_safely, slow_save_taxonomy
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS
from corgi.tasks.sca import cpu_software_composition_analysis

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_pyxis_manifest(
    oid: str,
    save_product: bool = True,
    force_process: bool = False,
) -> bool:
    """Fetch a pyxis manifest from pyxis"""
    logger.info("Fetching pyxis manifest %s", oid)

    # Fetch and parse the SBOM
    data = get_manifest_data(oid)

    image_id = data["image"]["_id"]

    result = False
    repositories = data["image"]["repositories"]
    logger.info("Manifest %s is related to %i repositories", oid, len(repositories))
    for repository in repositories:
        result = result or _slow_fetch_pyxis_manifest_for_repository(
            oid, image_id, repository, data, save_product, force_process
        )
    return result


def _slow_fetch_pyxis_manifest_for_repository(
    oid: str,
    image_id: str,
    repository: Mapping[str, Any],
    manifest: Mapping[str, Any],
    save_product: bool = True,
    force_process: bool = False,
) -> bool:

    # According to the PURL spec, the name part is the last fragment of the repository url
    component_name = repository["repository"].split("/")[-1]
    repository_url = f"{repository['registry']}/{repository['repository']}"
    logger.info("Processing slow fetch of %s - %s", repository_url, component_name)

    completion_time = manifest.get("creation_date", "")
    if completion_time:
        dt = dateparse.parse_datetime(completion_time.split(".")[0])
        if dt:
            completion_dt = make_aware(dt)
        else:
            raise ValueError(f"Could not parse completion_time for build {image_id}")
    else:
        # This really shouldn't happen
        raise ValueError(f"No completion_time for build {image_id}")

    softwarebuild, build_created = SoftwareBuild.objects.get_or_create(
        build_id=image_id,
        build_type=SoftwareBuild.Type.PYXIS,
        defaults={
            "completion_time": completion_dt,
            "name": component_name,
            "source": manifest["image"].get("source", ""),
            # Arbitrary dict can go here
            "meta_attr": {
                "image_id": image_id,
                "incompleteness_reasons": manifest["incompleteness_reasons"],
                "manifest_id": oid,
                "org_id": manifest["org_id"],
                "repository": repository,
            },
        },
    )
    if build_created:
        # Create foreign key from Relations to the new SoftwareBuild, where they don't already exist
        ProductComponentRelation.objects.filter(
            build_id=image_id, build_type=SoftwareBuild.Type.PYXIS, software_build__isnull=True
        ).update(software_build=softwarebuild)

    if not force_process and not build_created:
        # If another task starts while this task is downloading data this can result in processing
        # the same build twice, let's just bail out here to save cpu
        logger.warning("SoftwareBuild with build_id %s already existed, not reprocessing", image_id)
        return False

    # Use the creation time of the manifest entry in pyxis as a monotonically increasing version
    # identifier. TODO once RHTAP-1590 completes, revisit this and parse the tag to get the version
    component_version = str(int(completion_dt.timestamp()))

    root_node, root_created = save_container(
        softwarebuild, component_name, repository_url, component_version, manifest
    )

    # Save product taxonomy
    if save_product:
        logger.info("Requesting persistance of node structure for %s", softwarebuild.pk)
        slow_save_taxonomy.delay(softwarebuild.build_id, softwarebuild.build_type)

    if settings.SCA_ENABLED:
        logger.info("Requesting software composition analysis for %s", softwarebuild.pk)
        cpu_software_composition_analysis.delay(str(softwarebuild.pk), force_process=force_process)

    logger.info(
        "Created build (%s) or root (%s) for pyxis image: (%s, %s)",
        build_created,
        root_created,
        image_id,
        SoftwareBuild.Type.PYXIS,
    )
    logger.info("Finished fetching pyxis image: (%s, %s)", image_id, SoftwareBuild.Type.PYXIS)
    return build_created or root_created


def save_container(
    softwarebuild: SoftwareBuild,
    component_name: str,
    repository_url: str,
    component_version: str,
    manifest: Mapping[str, Any],
) -> tuple[ComponentNode, bool]:
    obj, root_created = Component.objects.update_or_create(
        type=Component.Type.CONTAINER_IMAGE,
        name=component_name,
        version=component_version,
        release="",
        arch="noarch",
        defaults={
            "namespace": Component.Namespace.REDHAT,
            "related_url": repository_url,
            "software_build": softwarebuild,
        },
    )

    root_node, root_node_created = save_node(ComponentNode.ComponentNodeType.SOURCE, None, obj)
    anything_else_created = False

    for component in manifest["edges"]["components"]["data"]:
        save_component(component, root_node)

    return root_node, (root_created or root_node_created or anything_else_created)


def save_component(component: dict, parent: ComponentNode) -> bool:
    logger.debug("Called save component with component %s", component)
    purl = component["purl"]
    if not purl:
        logger.warning("Cannot process component with no purl (%s)", purl)
        return False

    if not purl.startswith("pkg:"):
        raise ValueError(f"Encountered unrecognized purl prefix {purl}")
    component_type = purl[4:].split("/")[0].upper()

    if component_type not in Component.Type.values:
        raise ValueError(f"Tried to create component with invalid component_type: {component_type}")

    # A related url can be only one of any number of externalReferences provided. Pick the first.
    related_url = ""
    for reference in component["external_references"] or []:
        related_url = reference["url"]
        break

    name = component.pop("name") or ""
    version = component.pop("version") or ""
    license_declared_raw = component.pop("licenses") or ""

    # Grab release from the syft properties if available
    release = ""
    for prop in component["properties"]:
        if prop["name"] == "syft:metadata:release":
            release = prop["value"]

    epoch = 0
    for prop in component["properties"]:
        if prop["name"] == "syft:metadata:epoch":
            epoch = int(prop["value"])

    # Attempt to parse arch out of the purl querystring
    arch = "noarch"
    if "?" in purl:
        querystring = purl.split("?")[-1]
        values = urllib.parse.parse_qs(querystring)
        arch = values.get("arch", [arch])[0]

    if component.pop("publisher", "") == "Red Hat, Inc.":
        namespace = Component.Namespace.REDHAT
    else:
        namespace = Component.Namespace.UPSTREAM

    defaults = {
        "epoch": epoch,
        "namespace": namespace,
        "related_url": related_url,
    }

    with transaction.atomic():
        obj, created = Component.objects.update_or_create(
            type=component_type,
            name=name,
            version=version,
            release=release,
            arch=arch,
            defaults=defaults,
        )

        # Save the remaining attributes
        props = component.pop("properties", [])
        props = dict([(prop["name"], prop["value"]) for prop in props])
        obj.meta_attr = obj.meta_attr | component | props
        obj.save()

    # Wait until after transaction so obj lookup / atomic update succeeds
    # We don't set this above in case the value from Pyxis is empty
    # Otherwise we could overwrite a value submitted from OpenLCS
    set_license_declared_safely(obj, license_declared_raw)

    node, node_created = save_node(ComponentNode.ComponentNodeType.PROVIDES, parent, obj)
    return created or node_created

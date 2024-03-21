from urllib.parse import parse_qs

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db import transaction
from django.utils import dateparse
from django.utils.timezone import make_aware

from config.celery import app
from corgi.collectors.brew import Brew
from corgi.collectors.models import CollectorPyxisImage
from corgi.collectors.pyxis import get_manifest_data, get_repo_by_nvr
from corgi.core.constants import CONTAINER_REPOSITORY
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    SoftwareBuild,
)
from corgi.tasks.common import (
    RETRY_KWARGS,
    RETRYABLE_ERRORS,
    save_node,
    set_license_declared_safely,
    slow_save_taxonomy,
)
from corgi.tasks.sca import cpu_software_composition_analysis

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS, priority=6)
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
    # Handle case when key is present but value is None
    repositories = data["image"]["repositories"] or ()
    logger.info("Manifest %s is related to %i repositories", oid, len(repositories))
    for repository in repositories:
        result |= _slow_fetch_pyxis_manifest_for_repository(
            oid, image_id, repository, data, save_product, force_process
        )
    return result


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS, priority=6)
def slow_fetch_pyxis_image_by_nvr(nvr: str, force_process=False) -> str:
    """Checks if the image exist in the cache and only fetch if not, or force_process is true"""
    repo_names = list(
        CollectorPyxisImage.objects.filter(nvr=nvr).values_list("repos__name", flat=True)
    )
    if not repo_names or force_process:
        return get_repo_by_nvr(nvr)
    elif len(repo_names) > 1:
        raise ValueError(f"Found more than one repository matching nvr {nvr}")
    return repo_names[0]


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS, priority=6)
def slow_update_name_for_container_from_pyxis(nvr: str) -> bool:
    """Fetch a pyxis image and update the container components with the result"""
    repo_name = slow_fetch_pyxis_image_by_nvr(nvr)
    if not repo_name:
        return False
    name = repo_name.rsplit("/", 1)[-1]
    repository_url = f"{CONTAINER_REPOSITORY}/{repo_name}"
    # Look it existing containers with the NVR and save them.
    containers_to_update = Component.objects.filter(type=Component.Type.CONTAINER_IMAGE, nvr=nvr)
    for container in containers_to_update:
        if (
            container.name != name
            or container.meta_attr["repository_url"] != repository_url
            or container.related_url != repository_url
        ):
            container.name = name
            container.related_url = repository_url
            container.meta_attr["repository_url"] = repository_url
            container.save()
    return True


def _slow_fetch_pyxis_manifest_for_repository(
    oid: str,
    image_id: str,
    repository: dict,
    manifest: dict,
    save_product: bool = True,
    force_process: bool = False,
) -> bool:

    # According to the PURL spec, the name part is the last slash-separated path part
    # of the repository url
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
    # Create foreign key from Relations to the new SoftwareBuild, where they don't already exist
    ProductComponentRelation.objects.filter(
        build_id=softwarebuild.build_id,
        build_type=softwarebuild.build_type,
        software_build_id__isnull=True,
    ).update(software_build_id=softwarebuild.pk)

    if not force_process and not build_created:
        # TODO: We use singleton tasks, do we still need this logic?
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

    # TODO: manifest["image"]["source"] is not always present
    if settings.SCA_ENABLED and softwarebuild.source:
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
    manifest: dict,
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
        anything_else_created |= save_component(component, root_node)

    return root_node, (root_created or root_node_created or anything_else_created)


def save_component(component: dict, parent: ComponentNode) -> bool:
    """Save a component found in a Pyxis manifest"""
    logger.debug(f"Called Pyxis save component with component: {component}")
    purl = component.get("purl", "")
    if not purl:
        # We can't raise an error here, since some "components" are really products
        # so this error is expected for certain data in the manifest
        logger.warning(f"Cannot process component with no purl: {component}")
        return False

    if not purl.startswith("pkg:"):
        raise ValueError(f"Encountered unrecognized purl prefix {purl}")
    component_type = purl[4:].split("/")[0].upper()

    if component_type not in Component.Type.values:
        raise ValueError(f"Tried to create component with invalid component_type: {component_type}")

    # A related url can be only one of any number of externalReferences provided, prefer a website.
    # Handle case when key is present but value is None
    external_references = component.get("external_references", ()) or ()
    for reference in external_references:
        if reference["type"] == "website":
            related_url = reference["url"]
            break

    # None of the references were a website, so just pick the first.
    else:
        related_url = external_references[0]["url"] if external_references else ""

    name = component.pop("name") or ""
    version = component.pop("version") or ""
    # TODO: Is the data format of below correct?
    license_declared_raw = component.pop("licenses") or ""

    # Grab release from the syft properties if available
    # Handle case when key is present but value is None
    release = ""
    for prop in component.get("properties", ()) or ():
        if prop["name"] == "syft:metadata:release":
            release = prop["value"]

    # Grab epoch from the syft properties if available
    # Handle case when key is present but value is None
    epoch = 0
    for prop in component.get("properties", ()) or ():
        if prop["name"] == "syft:metadata:epoch":
            epoch = int(prop["value"])

    # Attempt to parse arch out of the purl querystring
    arch = "noarch"
    if "?" in purl:
        querystring = purl.split("?")[-1]
        values = parse_qs(querystring)
        # Names may have multiple values if a parameter appears more than once
        # e.g. ?arch=1&arch=2, so just take the first value if there are multiple
        # or else default to "noarch" if the "arch" parameter isn't present
        arch = values.get("arch", [arch])[0]

    defaults = {
        "epoch": epoch,
        "namespace": Brew.check_red_hat_namespace(
            component_type, version, component.pop("publisher", "")
        ),
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

        # Save the remaining attributes without transforming them or losing data
        # Handle case when key is present but value is None
        props = component.pop("properties", ()) or ()
        props = {"pyxis_properties": props}
        obj.meta_attr |= component | props
        obj.save()

    # Wait until after transaction so obj lookup / atomic update succeeds
    # We don't set this above in case the value from Pyxis is empty
    # Otherwise we could overwrite a value submitted from OpenLCS
    set_license_declared_safely(obj, license_declared_raw)

    node, node_created = save_node(ComponentNode.ComponentNodeType.PROVIDES, parent, obj)
    return created or node_created

import logging

import requests
from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.models import CollectorErrataProductVariant
from corgi.collectors.pnc import SbomerSbom
from corgi.core.models import Component, ComponentNode, SoftwareBuild
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_pnc_sbom(purl: str, product_data, build_data, sbom_data) -> None:
    logger.info("Fetching PNC SBOM %s for purl %s", sbom_data["id"], purl)
    """Fetch a PNC SBOM from sbomer"""

    # Validate the supplied product information
    try:
        CollectorErrataProductVariant.objects.get(name=product_data["productVariant"])
    except CollectorErrataProductVariant.DoesNotExist:
        logger.warning(
            "PNC SBOM provided for nonexistant product variant: %s", product_data["productVariant"]
        )
        return

    # Fetch and parse the SBOM
    response = requests.get(sbom_data["link"])
    response.raise_for_status()
    logger.info(f"SBOM fetched for purl {purl}")

    # Make sure the SBOM is valid
    sbom = SbomerSbom(response.json())

    # Create a build for the root component
    root_build, _ = SoftwareBuild.objects.get_or_create(
        build_id=sbom.components["root"]["meta_attr"]["pnc_build_id"],
        build_type=SoftwareBuild.Type.PNC,
    )

    # Create components
    components = {}
    for bomref, component in sbom.components.items():
        defaults = {"namespace": component["namespace"], "meta_attr": component["meta_attr"]}

        if "related_url" in component:
            defaults["related_url"] = component["related_url"]
        if bomref == "root":
            defaults["software_build"] = root_build

        components[bomref], _ = Component.objects.update_or_create(
            type=Component.Type.MAVEN,
            name=component["name"],
            version=component["version"],
            release="",
            arch="noarch",
            defaults=defaults,
        )

    # Link dependencies
    nodes = {}
    # Create the root node from the SBOM's component
    root_component = components.pop("root")
    nodes["root"], _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        defaults={"obj": root_component},
    )

    # Create the relationships recorded in the manifest's dependencies
    for parent, deps in sbom.dependencies.items():
        # Only create a new node for parent if it wasn't already
        # created as a dependency of another component
        if parent not in nodes:
            nodes[parent] = ComponentNode.objects.create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes["root"],
                purl=components[parent].purl,
                obj=components[parent],
            )

        # Deps may be children of both root and other nodes
        for dep in deps:
            nodes[dep] = ComponentNode.objects.create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes[parent],
                purl=components[dep].purl,
                obj=components[dep],
            )

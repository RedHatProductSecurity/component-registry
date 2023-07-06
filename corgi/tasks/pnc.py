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

    builds = {}
    for build in sbom.pnc_build_ids:
        builds[build], _ = SoftwareBuild.objects.get_or_create(
            build_id=build, build_type=SoftwareBuild.Type.PNC
        )

    for build in sbom.brew_build_ids:
        builds[build], _ = SoftwareBuild.objects.get_or_create(
            build_id=build, build_type=SoftwareBuild.Type.BREW
        )

    # Create components
    components = {}
    for component in sbom.components:
        components[component.bomref], _ = Component.objects.update_or_create(
            type=Component.Type.MAVEN,
            name=component["name"],
            version=component["version"],
            release="",
            arch="noarch",
            defaults={
                 "namespace": component["namespace"],
                 "softwarebuild": builds[component["build_id"]],
                 "related_url": component["related_url"],
                 "meta_attr": component["meta_attr"]
            },
        )

    # Link dependencies
    nodes = {}
    # Create the root node from the SBOM's component
    root_component = components.pop("root")
    nodes["root"], _ = ComponentNode.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=root_component.purl,
        defaults={"obj": root_component},
    )

    # Create the relationships recorded in the manifest's dependencies
    for parent, deps in sbom.dependencies.items():
        # Only create a new node for parent if it wasn't already
        # created as a dependency of another component
        if parent not in nodes:
            nodes[parent], _ = ComponentNode.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes["root"],
                purl=components[parent].purl,
                defaults={"obj": components[parent]},
            )

        for dep in deps:
            nodes[dep], _ = ComponentNode.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes[parent],
                purl=components[dep].purl,
                defaults={"obj": components[dep]},
            )

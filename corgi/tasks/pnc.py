import requests
from celery.utils.log import get_task_logger
from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import CollectorErrataProductVariant
from corgi.collectors.pnc import SbomerSbom
from corgi.core.models import (
    Component,
    ComponentNode,
    ProductComponentRelation,
    SoftwareBuild,
)
from corgi.tasks.brew import set_license_declared_safely, slow_save_taxonomy
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_pnc_sbom(purl: str, product_data: dict, sbom_data: dict) -> None:
    """Fetch a PNC SBOM from sbomer"""
    logger.info("Fetching PNC SBOM %s for purl %s", sbom_data["id"], purl)

    # Validate the supplied product information
    try:
        CollectorErrataProductVariant.objects.get(name=product_data["productVariant"])
    except CollectorErrataProductVariant.DoesNotExist:
        logger.warning(
            "PNC SBOM provided for nonexistent product variant: %s", product_data["productVariant"]
        )
        return

    # Fetch and parse the SBOM
    response = requests.get(sbom_data["link"])
    response.raise_for_status()
    logger.info(f"SBOM fetched for purl {purl}")

    # Make sure the SBOM is valid
    sbom_json = response.json()["sbom"]
    sbom = SbomerSbom(sbom_json)

    # Create a build for the root component
    root_build = SoftwareBuild.objects.create(
        build_id=sbom.components["root"]["meta_attr"]["pnc_build_id"],
        build_type=SoftwareBuild.Type.PNC,
    )

    # Create ProductComponentRelation
    ProductComponentRelation.objects.create(
        build_id=root_build.build_id,
        build_type=SoftwareBuild.Type.PNC,
        software_build=root_build,
        product_ref=sbom.product_variant,
        type=ProductComponentRelation.Type.SBOMER,
    )

    # Create components
    components = {}
    for bomref, component in sbom.components.items():
        defaults = {"namespace": component["namespace"], "meta_attr": component["meta_attr"]}

        if component.get("related_url"):
            defaults["related_url"] = component["related_url"]
        if bomref == "root":
            defaults["software_build"] = root_build

        if component["package_type"] == "maven":
            component_type = Component.Type.MAVEN
        else:
            component_type = Component.Type.GENERIC

        components[bomref], _ = Component.objects.update_or_create(
            type=component_type,
            name=component["name"],
            version=component["version"],
            release="",
            arch="noarch",
            defaults=defaults,
        )

        set_license_declared_safely(components[bomref], ";".join(component["licenses"]))

    # Link dependencies
    nodes = {}
    # Create the root node from the SBOM's component
    root_component = components.pop("root")
    nodes["root"] = ComponentNode.objects.create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=root_component.purl,
        obj=root_component,
    )

    # Create the relationships recorded in the manifest's dependencies
    for parent, deps in sbom.dependencies.items():
        # Only create a new node for parent if it wasn't already
        # created as a dependency of another component
        if parent not in nodes:
            nodes[parent], _ = ComponentNode.objects.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes["root"],
                purl=components[parent].purl,
                defaults={
                    "obj": components[parent],
                },
            )

        # Deps may be children of both root and other nodes
        for dep in deps:
            nodes[dep], _ = ComponentNode.objects.get_or_create(
                type=ComponentNode.ComponentNodeType.PROVIDES,
                parent=nodes[parent],
                purl=components[dep].purl,
                defaults={
                    "obj": components[dep],
                },
            )

    # Save product taxonomy
    slow_save_taxonomy.delay(root_build.build_id, root_build.build_type)

    # Save component taxonomy
    root_component.save_component_taxonomy()


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_handle_pnc_errata_released(erratum_id: int, erratum_status: str) -> None:
    # Check that the erratum is released
    if erratum_status != "SHIPPED_LIVE":
        raise ValueError(f"Invalid status {erratum_status} for erratum {erratum_id}")

    # Get the purl(s) of the related component(s) from the erratum's Notes field
    et = ErrataTool()
    notes = et.get_erratum_notes(erratum_id)

    related_purls = set()
    for ref in notes.get("manifest", {}).get("refs", []):
        if ref["type"] == "purl":
            related_purls.add(ref["uri"])

    if not related_purls:
        raise ValueError(f"Erratum {erratum_id} had no associated purls")

    # Check that there's a component matching the purls
    root_components: set[Component] = set()
    for purl in related_purls:
        # There should only be one root component of SOURCE type
        components = Component.objects.filter(
            type=Component.Type.MAVEN, meta_attr__purl_declared=purl
        )
        component_count = components.count()
        if component_count == 0:
            logger.warning(
                f"Erratum {erratum_id} refers to purl {purl} which matches no components"
            )
            continue
        if component_count > 1:
            logger.warning(
                f"Erratum {erratum_id} refers to purl {purl} which matches {component_count}"
                " components; only handling the first"
            )
        root_components.add(components[0])

    # Update relations with this erratum
    for component in root_components:
        if component.software_build is None:
            raise ValueError(f"Component {component.purl} has no build, can't relate {erratum_id}")

        build = component.software_build
        build_relation = ProductComponentRelation.objects.get(software_build=build)
        ProductComponentRelation.objects.update_or_create(
            external_system_id=erratum_id,
            product_ref=build_relation.product_ref,
            build_id=build.build_id,
            build_type=build.build_type,
            defaults={
                "type": ProductComponentRelation.Type.ERRATA,
                "meta_attr": {"component_purl": component.purl},
            },
        )

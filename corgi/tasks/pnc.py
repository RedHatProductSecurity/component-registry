from urllib.parse import unquote

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
    logger.info(f"Fetching PNC SBOM {sbom_data['id']} for purl {purl} from {sbom_data['link']}")

    # Validate the supplied product information
    CollectorErrataProductVariant.objects.get(name=product_data["productVariant"])

    # Fetch and parse the SBOM
    response = requests.get(sbom_data["link"])
    response.raise_for_status()
    logger.info(f"SBOM fetched for purl {purl}")

    # Make sure the SBOM is valid
    sbom_json = response.json()["sbom"]
    sbom = SbomerSbom(sbom_json)

    # Create a build for the root component
    root_build, _ = SoftwareBuild.objects.get_or_create(
        build_id=sbom.components["root"]["meta_attr"]["pnc_build_id"],
        build_type=SoftwareBuild.Type.PNC,
    )

    # Create ProductComponentRelation
    pcr, _ = ProductComponentRelation.objects.get_or_create(
        build_id=root_build.build_id,
        build_type=root_build.build_type,
        product_ref=sbom.product_variant,
        external_system_id=sbom_data["id"],
        defaults={
            "software_build": root_build,
            "type": ProductComponentRelation.Type.SBOMER,
        },
    )

    # Create components
    components = {}
    for bomref, component in sbom.components.items():
        defaults = {"namespace": component["namespace"], "meta_attr": component["meta_attr"]}

        if component.get("related_url"):
            defaults["related_url"] = component["related_url"]
        if bomref == "root":
            defaults["software_build"] = root_build

        if component["package_type"] == "maven" or bomref == "root":
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

        set_license_declared_safely(components[bomref], " OR ".join(component["licenses"]))

    # Create ComponentNodes
    # Create the root node from the SBOM's component
    root_component = components.pop("root")
    root_node, _ = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.SOURCE,
        parent=None,
        purl=root_component.purl,
        defaults={
            "obj": root_component,
        },
    )

    # CORGI-880: Record all included components as direct children of
    # the root, rather than recreating the relationships in the
    # CycloneDX manifest
    for component in components.values():
        node, _ = ComponentNode.objects.get_or_create(
            type=ComponentNode.ComponentNodeType.PROVIDES,
            parent=root_node,
            purl=component.purl,
            defaults={
                "obj": component,
            },
        )

    # Save product and component taxonomies
    slow_save_taxonomy.delay(root_build.build_id, root_build.build_type)


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
            # repository_urls may be percent-encoded
            related_purls.add(unquote(ref["uri"]))

    if not related_purls:
        raise ValueError(f"Erratum {erratum_id} had no associated purls")

    # Check that there's a component matching the purls
    root_components: set[Component] = set()
    for purl in related_purls:
        logger.info(
            f"Trying to match purl {purl} from erratum {erratum_id}'s Notes field to a Corgi purl"
        )
        # There is only one root component in Corgi matching the purl from ET
        # But the SBOMer purl_declared won't match purls in Corgi / ET's notes field
        # SBOMer copies its purls from PNC builds, before artifacts are released
        # (if they're ever released)
        # so SBOMer / PNC purls won't contain any ?repository_url= qualifier
        # Customers need this, so Corgi / the CVE-mapping generator must always add it
        # and purls from SBOMer / PNC will always be slightly different
        # To find purls mentioned in shipped errata, search using Corgi's purl field
        # instead of SBOMer's meta_attr__purl_declared field
        root_component = Component.objects.get(purl=purl)
        root_components.add(root_component)

    # Update relations with this erratum
    for component in root_components:
        if component.software_build is None:
            raise ValueError(f"Component {component.purl} has no build, can't relate {erratum_id}")

        build = component.software_build
        build_relation = ProductComponentRelation.objects.get(
            type=ProductComponentRelation.Type.SBOMER, software_build=build
        )
        ProductComponentRelation.objects.update_or_create(
            external_system_id=erratum_id,
            product_ref=build_relation.product_ref,
            build_id=build.build_id,
            build_type=build.build_type,
            defaults={
                "software_build": build,
                "type": ProductComponentRelation.Type.ERRATA,
            },
        )
        # Save product and component taxonomies
        slow_save_taxonomy.delay(build.build_id, build.build_type)

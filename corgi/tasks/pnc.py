import logging
from urllib.parse import urlparse

import requests
from celery_singleton import Singleton

from config.celery import app
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
    pedigrees = {}  # Support pedigree reports, see CORGI-796
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

        # If this component has a pedigree, create a component for that as well
        if component["meta_attr"].get("pedigree"):
            # Find upstream commits in pedigree history
            # Components might have more than one pedigree commit, but because
            # of uniqueness constraints, only Github upstreams are created here,
            # and then only for the first commit in the list. See CORGI-796 for
            # more information.
            for commit in component["meta_attr"]["pedigree"].get("commits"):
                host = urlparse(commit["url"]).hostname
                # Only create pedigree components for external sources
                if not host.endswith("redhat.com"):
                    if host.endswith("github.com"):
                        pedigree_type = Component.Type.GITHUB
                    else:
                        logger.warning(
                            "Component %s has non-Github external pedigree, skipping it."
                        )
                        continue

                    pedigree, _ = Component.objects.get_or_create(
                        type=pedigree_type,
                        name=component["name"],
                        version=component["version"],
                        release="",
                        arch="noarch",
                        defaults={
                            "namespace": Component.Namespace.UPSTREAM,
                            "meta_attr": {
                                "commit": commit,
                            },
                        },
                    )

                    # Components might have more than one pedigree commit
                    # Because of component uniqueness constraints, k
                    if bomref not in pedigrees:
                        pedigrees[bomref] = [pedigree]
                    else:
                        pedigrees[bomref].append(pedigree)

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

    # Create relationships for pedigree components
    for component, commits in pedigrees.items():
        for commit in commits:
            ComponentNode.objects.create(
                type=ComponentNode.ComponentNodeType.SOURCE,
                parent=nodes[component],
                purl=components[parent].purl,
                obj=commit,
            )

    # Save product taxonomy
    slow_save_taxonomy.delay(root_build.build_id, root_build.build_type)

    # Save component taxonomy
    root_component.save_component_taxonomy()

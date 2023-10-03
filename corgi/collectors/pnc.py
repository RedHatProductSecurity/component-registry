import logging
from typing import Any

from corgi.core.constants import SBOMER_PRODUCT_MAP
from corgi.core.models import Component

logger = logging.getLogger(__name__)


class SbomerSbom:
    """Collector to parse SBOMs from PNC/Sbomer. See CORGI-488.
    Future development should generalize this where applicable
    to other middleware."""

    def __init__(self, data: dict[str, Any]):
        # Product information
        root_meta = {
            param["name"]: param["value"] for param in data["metadata"]["component"]["properties"]
        }
        self.product = root_meta["errata-tool-product-name"]
        self.product_version = root_meta["errata-tool-product-version"]
        self.product_variant = root_meta["errata-tool-product-variant"]

        if "components" not in data:
            raise ValueError("SBOM is missing component data")

        # Components is a list of all components. Some are listed
        # more than once with different bomrefs, as they're listed
        # separately for each dependency they have or fulfill.
        self.components = {c["bom-ref"]: c for c in data["components"]}
        # The root component is listed separately in metadata
        self.components["root"] = data["metadata"]["component"]
        for component in self.components.values():
            meta_attr = {}

            # Red Hat components should declare a supplier; upstream
            # components may not. Upstream components won't have
            # build info
            if "supplier" in component and component["supplier"].get("name") == "Red Hat":
                component["namespace"] = Component.Namespace.REDHAT
                build_urls = {
                    ref["comment"]: ref["url"]
                    for ref in component.get("externalReferences", {})
                    if ref["type"] == "build-system"
                }

                if "pnc-build-id" in build_urls:
                    meta_attr["pnc_build_id"] = build_urls["pnc-build-id"].split("/")[-1]
                if "brew-build-id" in build_urls:
                    meta_attr["brew_build_id"] = build_urls["brew-build-id"].split("/")[-1]
            else:
                component["namespace"] = Component.Namespace.UPSTREAM

            meta_attr["group_id"] = component["group"]

            # Declared purl
            meta_attr["purl_declared"] = component["purl"]

            # License info
            licenses = []
            for _license in component.get("licenses", ()):
                if _license["license"].get("id"):
                    licenses.append(_license["license"].get("id"))
                if _license["license"].get("name"):
                    licenses.append(_license["license"].get("name"))

            component["licenses"] = licenses

            # Related URLs
            component["related_url"] = None
            url_types = ["website", "distribution", "issue-tracker", "mailing-list", "vcs"]
            for ref in component.get("externalReferences", {}):
                if ref["type"] in url_types:
                    meta_attr[f"{ref['type']}_ref_url"] = ref["url"]

                    # If there hasn't been a better related_url yet, use this one
                    if not component["related_url"]:
                        component["related_url"] = ref["url"]

            # Pedigree info when it's available
            if component.get("pedigree"):
                meta_attr["pedigree"] = component["pedigree"]

            # Package type info when it's available
            component["package_type"] = None
            package_types = [
                prop for prop in component["properties"] if prop["name"] == "package:type"
            ]
            if package_types:
                if len(package_types) > 1:
                    logger.warn("Component %s had multiple package types, taking the first")
                component["package_type"] = package_types[0]["value"]

            component["meta_attr"] = meta_attr

        # Dependencies is a list of relationships between components
        # declared in the Components section above. At the moment,
        # only "dependsOn" type relationships are declared.
        self.dependencies = {d["ref"]: d["dependsOn"] for d in data["dependencies"]}


def is_sbomer_product(product: str, product_release: str) -> bool:
    """Identifies products for which SBOMer produces manifests and which need separate handling
    for release errata"""
    return product in SBOMER_PRODUCT_MAP and product_release in SBOMER_PRODUCT_MAP[product]

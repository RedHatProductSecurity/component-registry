import logging
from collections.abc import Mapping
from typing import Any

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class SbomerSbom:
    """Collector to parse SBOMs from PNC/Sbomer. See CORGI-488.
    Future development should generalize this where applicable
    to other middleware."""

    def __init__(self, data: Mapping[str, Any]):
        # Product information
        root_meta = {
            param["name"]: param["value"] for param in data["metadata"]["component"]["properties"]
        }
        self.product = root_meta["errata-tool-product-name"]
        self.product_version = root_meta["errata-tool-product-version"]
        self.product_variant = root_meta["errata-tool-product-variant"]

        if "components" not in data:
            raise ValueError("SBOM is missing component data")

        self.components = {c["bom-ref"]: c for c in data["components"]}
        # The root component is listed separately in metadata
        self.components["root"] = data["metadata"]["component"]
        for bomref, component in self.components.items():
            self.components[bomref]["meta_attr"] = {}
            if "redhat" not in component["purl"]:
                # Community components won't have build info
                self.components[bomref]["namespace"] = Component.Namespace.UPSTREAM
            else:
                self.components[bomref]["namespace"] = Component.Namespace.REDHAT
                build_urls = {
                    ref["comment"]: ref["url"]
                    for ref in component["externalReferences"]
                    if ref["type"] == "build-system"
                }

                if "pnc-build-id" in build_urls:
                    self.components[bomref]["meta_attr"]["pnc_build_id"] = build_urls[
                        "pnc-build-id"
                    ].split("/")[-1]
                if "brew-build-id" in build_urls:
                    self.components[bomref]["meta_attr"]["brew_build_id"] = build_urls[
                        "brew-build-id"
                    ].split("/")[-1]

            # Declared purl
            self.components[bomref]["meta_attr"]["purl_declared"] = component["purl"]

            # Related URL
            website = [
                ref["url"] for ref in component["externalReferences"] if ref["type"] == "website"
            ]
            if website:
                self.components[bomref]["related_url"] = website[0]

            # VCS URL
            vcs = [ref["url"] for ref in component["externalReferences"] if ref["type"] == "vcs"]
            if vcs:
                # TODO: Should this be called something else? "remote_sources"?
                self.components[bomref]["meta_attr"]["vcs"] = vcs[0]

            # Pedigree info when it's available
            if "pedigree" in component:
                self.components[bomref]["meta_attr"]["pedigree"] = component["pedigree"]

        self.dependencies = {d["ref"]: d["dependsOn"] for d in data["dependencies"]}

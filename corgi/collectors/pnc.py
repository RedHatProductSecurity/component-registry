import logging
from collections.abc import Mapping
from typing import Any

from corgi.core.models import Component, SoftwareBuild

logger = logging.getLogger(__name__)


class SbomerSbom:
    """Collector to parse SBOMs from PNC/Sbomer. See CORGI-488.
    Future development should generalize this where applicable
    to other middleware."""

    def __init__(self, data: Mapping[str, Any]):
        if "components" not in data:
            raise ValueError("SBOM is missing component data")

        self.components = {c["bom-ref"]: c for c in data["components"]}
        # The root component is listed separately in metadata
        self.components["root"] = data["metadata"]["component"]
        # Provide easier lookup for data required by Corgi
        self.pnc_build_ids = set()
        self.brew_build_ids = set()
        for bomref, component in self.components.items():
            self.components[bomref]["meta_attr"] = {}
            if "redhat" not in component["purl"]:
                # Community components won't have build info
                self.components[bomref]["namespace"] = Component.Namespace.UPSTREAM
            else:
                self.components[bomref]["namespace"] = Component.Namespace.REDHAT
                # Build IDs: If there's no PNC build, there should be a
                # fallback Brew build
                build_urls = {
                    ref["comment"]: ref["url"]
                    for ref in component["externalReferences"]
                    if ref["type"] == "build-system"
                }

                # Everything built within Red Hat should have *some* build info
                if not build_urls:
                    # FIXME: Current manifests don't always have build info
                    raise ValueError("Component has no builds")
                else:
                    pnc_build_id = None
                    brew_build_id = None
                    # One or both of PNC & Brew builds should be present
                    # If it's present, the PNC build should be the primary build
                    if "pnc-build-id" in build_urls:
                        pnc_build_id = build_urls["pnc-build-id"].split("/")[-1]
                        self.pnc_build_ids.add(pnc_build_id)
                    if "brew-build-id" in build_urls:
                        brew_build_id = build_urls["brew-build-id"].split("/")[-1]
                        self.brew_build_ids.add(brew_build_id)
                    if pnc_build_id:
                        self.components[bomref]["build_id"] = {
                            "type": SoftwareBuild.Type.PNC,
                            "id": pnc_build_id,
                        }
                        if brew_build_id:  # Add Brew build as meta info
                            self.components[bomref]["meta_attr"]["brew_build_id"] = brew_build_id
                    elif brew_build_id:  # No PNC build info, use Brew as primary build
                        self.components[bomref]["build_id"] = {
                            "type": SoftwareBuild.Type.BREW,
                            "id": brew_build_id,
                        }
                    else:
                        raise ValueError("Component has unknown build type")

            # Related URL
            website = [
                ref["url"] for ref in component["externalReferences"] if ref["type"] == "website"
            ]
            if website:
                self.components[bomref]["related_url"] = website[0]

        self.dependencies = {d["ref"]: d["dependsOn"] for d in data["dependencies"]}

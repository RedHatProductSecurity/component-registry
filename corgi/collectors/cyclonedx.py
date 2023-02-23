import json
import logging
from pathlib import Path
from typing import Any, Iterator

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class CycloneDxSbom:
    @classmethod
    def parse(cls, path: Path) -> Iterator[dict[str, Any]]:
        with open(path) as sbom:
            contents = json.load(sbom)

        for comp in contents["components"]:
            properties = {}
            for p in comp["properties"]:
                properties.update({p["name"]: p["value"]})

            c: dict[str, Any] = {
                "meta": {
                    "name": comp["name"],
                    "version": comp["version"],
                    "derived_purl": comp["purl"],
                    "group_id": comp["group"],
                }
            }

            # Description isn't always present
            c["meta"]["description"] = comp.get("description", comp["name"])

            # Only handle Maven packages for now
            if properties["package:type"] == "maven":
                c["type"] = Component.Type.MAVEN
            else:
                logger.warning("Unknown type in CycloneDX SBOM: %s", properties["package:type"])

            yield c

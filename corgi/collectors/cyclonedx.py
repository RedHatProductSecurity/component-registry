import json
import logging
from pathlib import Path
from typing import Any, Iterator

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class CycloneDxSbom:
    @classmethod
    def parse(cls, data: str) -> Iterator[dict[str, Any]]:
        contents = json.loads(data)

        for comp in contents["components"]:
            properties = {}
            for p in comp["properties"]:
                properties.update({p["name"]: p["value"]})

            licenses = cls.build_license_list(comp["licenses"])

            # Description isn't always present
            description = comp.get("description", comp["name"])

            c: dict[str, Any] = {
                "meta": {
                    "name": comp["name"],
                    "version": comp["version"],
                    "description": description,
                    "declared_purl": comp["purl"],
                    "group_id": comp["group"],
                    "declared_licenses": licenses,
                }
            }

            # Only handle Maven packages for now
            if properties["package:type"] == "maven":
                c["type"] = Component.Type.MAVEN
            else:
                logger.warning("Unknown type in CycloneDX SBOM: %s", properties["package:type"])

            yield c

    @classmethod
    def parse_file(cls, path: Path) -> Iterator[dict[str, Any]]:
        with open(path) as sbom_file:
            contents = sbom_file.read()
        return cls.parse(contents)

    @classmethod
    def build_license_list(cls, licenses: list[dict[str, dict[str, str]]]) -> str:
        license_names = []
        for lic in licenses:
            # This loses information like license URLs
            name = lic["license"].get("id", lic["license"].get("name"))
            if name:
                license_names.append(name)

        return " AND ".join(license_names)

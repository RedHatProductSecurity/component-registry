import json
import logging
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from packageurl import PackageURL

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class Syft:
    # Syft packages types https://github.com/anchore/syft/
    # blob/v0.60.1/syft/pkg/type.go#L8
    SYFT_PKG_TYPE_MAPPING = {
        "go-module": Component.Type.GOLANG,
        "npm": Component.Type.NPM,
        "python": Component.Type.PYPI,
        "java-archive": Component.Type.MAVEN,
        "rpm": Component.Type.RPM,
        "gem": Component.Type.GEM,
        "rust-crate": Component.Type.CARGO,
    }

    @classmethod
    def scan_files(cls, target_files: list[Path]) -> list[dict[str, Any]]:
        scan_results: list[dict[str, Any]] = []
        for target_file in target_files:
            if target_file.is_file():
                scheme = "file"
            elif target_file.is_dir():
                scheme = "dir"
            else:
                raise ValueError("Target file %s is not a file or a directory", target_file)
            # Exclude vendor directories as they sometimes produce extraneous results.
            # For now target_file is sanitized via url parsing and coming from Brew.
            # We might consider more adding more sanitization if we accept ad-hoc source for scans
            scan_result = subprocess.check_output(  # nosec B603
                [
                    "/usr/bin/syft",
                    "packages",
                    "-q",
                    "-o=syft-json",
                    "--exclude=**/vendor/**",
                    f"{scheme}:{target_file}",
                ],
                text=True,
            )
            scan_results.extend(cls.parse_components(scan_result))
        return scan_results

    @classmethod
    def parse_components(cls, syft_json):
        raw_result = json.loads(syft_json)
        components: list[dict[str, Any]] = []
        syft_version = ""
        if "descriptor" in raw_result:
            syft_version = raw_result["descriptor"].get("version", "")
        if "artifacts" in raw_result:
            # Syft packages types https://github.com/anchore/syft/
            # blob/v0.60.1/syft/pkg/type.go#L8
            for artifact in raw_result["artifacts"]:
                if artifact["type"] in cls.SYFT_PKG_TYPE_MAPPING:
                    pkg_type = cls.SYFT_PKG_TYPE_MAPPING[artifact["type"]]
                else:
                    logger.warning("Skipping unknown Syft type: %s", artifact["type"])
                    continue

                typed_component: dict[str, Any] = {
                    "type": pkg_type,
                    "meta": {
                        "name": artifact["name"],
                        "version": artifact["version"],
                        "purl": artifact["purl"],
                    },
                    "analysis_meta": {"source": f"syft-{syft_version}"},
                }

                if pkg_type == Component.Type.MAVEN:
                    purl = PackageURL.from_string(artifact["purl"])
                    typed_component["meta"]["group_id"] = purl.namespace

                components.append(typed_component)
        return components

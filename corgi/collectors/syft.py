import json
import logging
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class Syft:
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
                    "/usr/local/bin/syft",
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
            # Syft packages types https://github.com/anchore/syft/blob/
            # 73262c7258cac24cbccf38cb9b97b67091d8f830/syft/pkg/type.go#L8
            # TODO add gem type to models and match it with "gem"
            for artifact in raw_result["artifacts"]:
                if artifact["type"] == "go-module":
                    pkg_type = Component.Type.GOLANG
                elif artifact["type"] == "npm":
                    pkg_type = Component.Type.NPM
                elif artifact["type"] == "python":
                    pkg_type = Component.Type.PYPI
                elif artifact["type"] == "java-archive":
                    pkg_type = Component.Type.MAVEN
                elif artifact["type"] == "rpm":
                    pkg_type = Component.Type.RPM
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
                    "analysis_meta": {"source": "syft", "version": syft_version},
                }
                components.append(typed_component)
        return components

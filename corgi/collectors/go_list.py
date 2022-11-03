import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from splitstream import splitfile

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class GoList:
    @classmethod
    def scan_files(cls, target_paths: list[Path]) -> list[dict[str, Any]]:
        scan_results: list[dict[str, Any]] = []
        for target_path in target_paths:
            if not target_path.is_dir():
                raise ValueError("Target path %s is not a directory", target_path)
            # TODO parse_components accepts a file-like object, not string
            scan_result = subprocess.check_output(  # nosec B603
                ["/usr/bin/go", "list", "-json", "-deps", "./..."],
                cwd=target_path,
                text=True,
            )
            scan_results.extend(cls.parse_components(scan_result))
        return scan_results

    @classmethod
    def parse_components(cls, go_list_json):
        components: list[dict[str, Any]] = []

        # use of splitstream here as `go list` output is actually a stream of json objects,
        # not a fully formed valid json document
        for jsonstr in splitfile(go_list_json, format="json"):
            artifact = json.loads(jsonstr)
            typed_component: dict[str, Any] = {
                "type": Component.Type.GOLANG,
                "meta": {
                    "name": artifact["ImportPath"],
                },
                "analysis_meta": {"source": "go-list"},
            }
            if "Module" in artifact:
                if "Version" in artifact["Module"]:
                    typed_component["version"] = artifact["Module"]["Version"]

            components.append(typed_component)
        return components

import json
import logging
import subprocess  # nosec B404
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from django.conf import settings
from packageurl import PackageURL

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class Syft:
    # Syft packages types: https://github.com/anchore/syft/blob/v0.72.0/syft/pkg/type.go
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
                    # see motivation for excluding test/fixtures in CORGI-510
                    # For example skip test/fixtures/0-dns/package.json file in nodejs
                    "--exclude=**/vendor/**",
                    "--exclude=**/test/fixtures/**",
                    "--exclude=**/src/test/resources/**",
                    f"{scheme}:{target_file}",
                ],
                text=True,
            )
            scan_results.extend(cls.parse_components(scan_result))
        return scan_results

    @classmethod
    def scan_repo_image(cls, target_image: str) -> list[dict[str, Any]]:
        """Scan a remote container image

        `target_image` can point to a specific tag or a digest, e.g.:
        quay.io/somerepo/example-container:latest
        quay.io/somerepo/example-container@sha256:03103d6d7e04755319eb303953669182da42246397e32b30afee3f67ebd4d2bb

        Note that this method assumes authentication credentials are set in ~/.docker/config.json
        (or a location pointed to by DOCKER_CONFIG) for repositories in registries that are private.
        """
        scan_result = subprocess.check_output(  # nosec B603
            [
                "/usr/bin/syft",
                "packages",
                "-q",
                "-o=syft-json",
                "--exclude=**/vendor/**",
                f"registry:{target_image}",
            ],
            text=True,
        )
        parsed_components = cls.parse_components(scan_result)
        return parsed_components

    @classmethod
    def scan_git_repo(cls, target_url: str, target_ref: str = "") -> list[dict[str, Any]]:
        """Scan a source Git repository.

        An optional target ref can be specified that represents a valid committish in the Git
        repo being scanned.
        """
        with TemporaryDirectory(dir=settings.SCA_SCRATCH_DIR) as scan_dir:
            logger.info("Cloning %s to %s", target_url, scan_dir)
            subprocess.check_call(
                ["/usr/bin/git", "clone", target_url, scan_dir], stderr=subprocess.DEVNULL
            )  # nosec B603
            if target_ref:
                subprocess.check_call(  # nosec B603
                    ["/usr/bin/git", "checkout", target_ref],
                    cwd=scan_dir,
                    stderr=subprocess.DEVNULL,
                )
            scan_results = cls.scan_files(target_files=[Path(scan_dir)])
        return scan_results

    @classmethod
    def parse_components(cls, syft_json: str) -> list[dict[str, Any]]:
        raw_result = json.loads(syft_json)
        components: list[dict[str, Any]] = []
        syft_version = ""
        if "descriptor" in raw_result:
            syft_version = raw_result["descriptor"].get("version", "")
        if "artifacts" in raw_result:
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
                        "source": [f"syft-{syft_version}"],
                    },
                }

                if pkg_type == Component.Type.MAVEN:
                    purl = PackageURL.from_string(artifact["purl"])
                    typed_component["meta"]["group_id"] = purl.namespace
                elif pkg_type == Component.Type.GOLANG:
                    typed_component["meta"]["go_component_type"] = "gomod"

                components.append(typed_component)
        return components

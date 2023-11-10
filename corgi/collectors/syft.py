import json
import logging
import os
import subprocess  # nosec B404
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import requests
from django.conf import settings
from packageurl import PackageURL

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class GitCloneError(Exception):
    pass


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
                    # see motivation for excluding test/fixtures in CORGI-510, and CORGI-824
                    # For example skip test/fixtures/0-dns/package.json file in nodejs
                    # Skip test/fixtures
                    # Skip test/spec/*.spec in rpmlint
                    "--exclude=**/vendor/**",
                    "--exclude=**/test/**",
                    f"{scheme}:{target_file}",
                ],
                text=True,
            )
            components, _ = cls.parse_components(scan_result)
            scan_results.extend(components)
        return scan_results

    @classmethod
    def scan_repo_image(
        cls, target_image: str, target_host: str = "quay.io", token: str = settings.QUAY_TOKEN
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Scan a remote container image

        `target_image` can point to a general image name, a specific tag or a digest, e.g.:
        quay.io/somerepo/example-container
        quay.io/somerepo/example-container:latest
        quay.io/somerepo/example-container@sha256:03103d6d7e04755319eb303953669182da42246397e32b30afee3f67ebd4d2bb

        Note that this method assumes authentication credentials are set in ~/.docker/config.json
        (or a location pointed to by DOCKER_CONFIG) for repositories in registries that are private.
        """
        syft_args = [
            "/usr/bin/syft",
            "packages",
            "-q",
            "-o=syft-json",
            "--exclude=**/vendor/**",
            f"registry:{target_host}/{target_image}",
        ]

        try:
            scan_result = subprocess.check_output(syft_args, text=True)  # nosec B603
        except subprocess.CalledProcessError as e:
            # Some images cannot be pulled because they have no implicit "latest" tag
            # If a version / tag was already given explicitly, just fail here
            if ":" in target_image:
                raise e
            # Else append the most-recently updated tag to the image we try to pull
            target_version = cls.get_quay_repo_version(target_image, target_host, token)
            syft_args[-1] = f"{syft_args[-1]}:{target_version}"
            scan_result = subprocess.check_output(syft_args, text=True)  # nosec B603

        parsed_components, source_data = cls.parse_components(scan_result)
        return parsed_components, source_data

    @staticmethod
    def get_quay_repo_version(target_image: str, target_host: str, token: str) -> str:
        """Helper function to find a Quay image version / ref for a given repo name"""
        # We pull the "latest" tag by default, but not all images have one
        # App-interface data is too complex, so apps_v1.saasFiles.resourceTemplates.targets.ref
        # versions can't be matched to their corresponding apps_v1.quayRepos.items.name repos
        # Multiple resourceTemplates.targets also exist, but we only want the prod ones
        # There's no easy and reliable way to know if a target is for stage / should be skipped
        # So just make a separate query to the Quay API for this repo - it's much simpler

        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            # Assumes we have a Quay instance - either public Quay.io or internal
            f"https://{target_host}/api/v1/repository/{target_image}?includeTags=true",
            headers=headers,
        )
        response.raise_for_status()

        version = "latest"
        # Some images have no tags at all, so pulling the image will fail either way
        # If empty, the for-loop below won't be entered, so we default version to "latest"
        # to get a meaningful error when pulling instead of "NameError: version undefined" here
        for tag in response.json()["tags"]:
            # The tags key is a nested dict and not a list, so we can't just grab tags[0]
            # The first key in the dict is always the newest / most recently updated tag name
            version = tag
            break
        return version

    @classmethod
    def scan_git_repo(
        cls, target_url: str, target_ref: str = ""
    ) -> tuple[list[dict[str, Any]], str]:
        """Scan a source Git repository.

        An optional target ref can be specified that represents a valid committish in the Git
        repo being scanned.
        """
        # Convert unauthenticated / web-based URLs to SSH-based URLs
        # so that "git clone" uses our existing SSH key and doesn't prompt
        # for a username/password combo if the repo is private
        if target_url.startswith("https://github.com/"):
            target_url = target_url.replace("https://github.com/", "git@github.com:", 1)
        elif target_url.startswith("http://github.com/"):
            target_url = target_url.replace("http://github.com/", "git@github.com:", 1)
        # Else it could be an internal Gitlab server - we allow these to fail
        # They should be made into public repos that work without authentication
        with TemporaryDirectory(dir=settings.SCA_SCRATCH_DIR) as scan_dir:
            logger.info("Cloning %s to %s", target_url, scan_dir)
            # This may fail if we don't have access to the repository. GIT_TERMINAL_PROMPT=0
            # ensures that we don't hang the command on a prompt for a username and password.
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
            result = subprocess.run(
                ["/usr/bin/git", "clone", target_url, scan_dir],
                capture_output=True,
                timeout=120,  # seconds
                env=env,
            )  # nosec B603
            if result.returncode != 0:
                raise GitCloneError(
                    f"git clone of {target_url} failed with: {result.stderr.decode('utf-8')}"
                )

            if target_ref:
                subprocess.check_call(  # nosec B603
                    ["/usr/bin/git", "checkout", target_ref],
                    cwd=scan_dir,
                    stderr=subprocess.DEVNULL,
                )
                source_ref = target_ref
            else:
                source_ref = subprocess.check_output(
                    ["/usr/bin/git", "rev-parse", "HEAD"], cwd=scan_dir, text=True
                ).strip()  # nosec B603
            scan_results = cls.scan_files(target_files=[Path(scan_dir)])
        return scan_results, source_ref

    @classmethod
    def parse_components(cls, syft_json: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
                        "name": artifact["name"].strip(),
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
        return components, raw_result["source"]

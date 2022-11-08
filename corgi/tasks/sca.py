import logging
import re
import shutil
import subprocess  # nosec B404
import tarfile
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import requests
from celery_singleton import Singleton
from django.conf import settings
from requests import Response

from config.celery import app
from corgi.collectors.go_list import GoList
from corgi.collectors.syft import Syft
from corgi.core.models import Component, ComponentNode, SoftwareBuild
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

LOOKASIDE_SCRATCH_SUBDIR = "lookaside"
LOOKASIDE_REGEX_SOURCE_PATTERNS = [
    # https://regex101.com/r/xYoHtX/1
    r"^(?P<hash>[a-f0-9]*)[ ]+(?P<file>[a-zA-Z0-9.\-_]*)",
    # https://regex101.com/r/mjtKif/1
    r"^(?P<alg>[A-Z0-9]*) \((?P<file>[a-zA-Z0-9.-]*)\) = (?P<hash>[a-f0-9]*)",
]
lookaside_source_regexes = tuple(re.compile(p) for p in LOOKASIDE_REGEX_SOURCE_PATTERNS)
logger = logging.getLogger(__name__)


def save_component(component: dict[str, Any], parent: ComponentNode):
    meta = component.get("meta", {})
    if component["type"] not in Component.Type:
        logger.warning("Tried to save component with unknown type: %s", component["type"])

    meta_attr = component["analysis_meta"]

    if component["type"] == Component.Type.MAVEN:
        group_id = meta.get("group_id")
        if group_id:
            meta_attr["group_id"] = group_id

    # Use all fields from Component index and uniqueness constraint
    obj, created = Component.objects.get_or_create(
        type=component["type"],
        name=meta.pop("name", ""),
        version=meta.pop("version", ""),
        release="",
        arch="",
        defaults={"meta_attr": meta_attr},
    )

    if "purl" in meta and obj.purl != meta["purl"]:
        logger.warning(
            "Saved component purl %s, does not match Syft purl: %s", obj.purl, meta["purl"]
        )

    node, node_created = obj.cnodes.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=parent,
        purl=obj.purl,
        defaults={
            "object_id": obj.pk,
            "obj": obj,
        },
    )

    return created or node_created


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_software_composition_analysis(build_id: int):
    logger.info("Started software composition analysis for %s", build_id)
    software_build = SoftwareBuild.objects.get(build_id=build_id)

    # Get root component for this build; fail the task if it does not exist.
    # TODO: ditch type:ignore when https://github.com/typeddjango/django-stubs/pull/1025 is released
    root_component = (
        Component.objects.filter(software_build=software_build)
        .root_components()  # type:ignore
        .get()
    )

    if root_component.name == "kernel":
        logger.info("skipping scan of the kernel, see CORGI-270")
        return

    root_node = root_component.cnodes.first()
    if not root_node:
        raise ValueError(f"Didn't find root component node for {root_component.purl}")

    distgit_sources = _get_distgit_sources(software_build.source, build_id)

    no_of_new_components = _scan_files(root_node, distgit_sources)
    if no_of_new_components > 0:
        software_build.save_product_taxonomy()

    # clean up source code so that we don't have to deal with reuse and an ever growing disk
    for source in distgit_sources:
        rm_target = source
        if source.is_file():
            rm_target = source.parents[0]
        # Ignore errors because the dir might already be deleted due to multiples sources
        # being in the same directory
        shutil.rmtree(rm_target, ignore_errors=True)

    logger.info("Finished software composition analysis for %s", build_id)
    return no_of_new_components


def _scan_files(anchor_node, sources) -> int:
    logger.info(
        "Scan files called with anchor node: %s, and sources: %s", anchor_node.purl, sources
    )
    new_components = 0
    detected_components = Syft.scan_files(sources)
    # get go version from container meta_attr
    go_packages = GoList.scan_files(sources)
    _assign_go_stdlib_version(anchor_node.obj, go_packages)
    detected_components.extend(go_packages)

    for component in detected_components:
        if save_component(component, anchor_node):
            new_components += 1
    logger.info("Detected %s new components using Syft scan", new_components)
    return new_components


def _assign_go_stdlib_version(anchor_obj, go_packages):
    for go_package in go_packages:
        if (
            "version" not in go_package["meta"]
            and anchor_obj.type == Component.Type.CONTAINER_IMAGE
            and anchor_obj.arch == "noarch"
        ):
            if "go_stdlib_version" in anchor_obj.meta_attr:
                go_package["meta"]["version"] = anchor_obj.meta_attr["go_stdlib_version"]


def _get_distgit_sources(source_url: str, build_id: int) -> list[Path]:
    distgit_sources: list[Path] = []
    raw_source, package_type, package_name = _clone_source(source_url, build_id)
    if not raw_source:
        return []
    distgit_sources.append(raw_source)
    sources = _download_lookaside_sources(raw_source, build_id, package_type, package_name)
    distgit_sources.extend(sources)
    return distgit_sources


def _clone_source(source_url: str, build_id: int) -> Tuple[Path, str, str]:
    # (scheme, netloc, path, parameters, query, fragment)
    url = urlparse(source_url)

    # We only support git, git+https, git+ssh
    if not url.scheme.startswith("git"):
        raise ValueError("Cannot download raw source for anything but git protocol")

    git_remote = f"{url.scheme}://{url.netloc}{url.path}"
    path_parts = url.path.rsplit("/", 2)
    package_type = path_parts[1]
    package_name = path_parts[2]
    commit = url.fragment

    target_path = Path(f"{settings.SCA_SCRATCH_DIR}/{build_id}/")

    # Allow existing directory error to cause parent task to fail
    target_path.mkdir()

    logger.info("Fetching %s to %s", git_remote, target_path)
    subprocess.check_call(["/usr/bin/git", "clone", git_remote, target_path])  # nosec B603
    subprocess.check_call(  # nosec B603
        ["/usr/bin/git", "checkout", commit], cwd=target_path, stderr=subprocess.DEVNULL
    )
    return target_path, package_type, package_name


def _download_lookaside_sources(
    distgit_sources: Path, build_id: int, package_type: str, package_name: str
) -> list[Path]:
    lookaside_source = distgit_sources / "sources"
    if not lookaside_source.exists():
        logger.warning("No lookaside sources in %s", distgit_sources)
        return []

    with open(lookaside_source, "r") as source_content_file:
        source_content = source_content_file.readlines()

    downloaded_sources: list[Path] = []
    for line in source_content:
        match = None
        for regex in lookaside_source_regexes:
            match = regex.search(line)
            if match:
                break  # lookaside source regex loop
        if not match:
            continue  # source content loop
        lookaside_source_matches = match.groupdict()
        lookaside_source_filename = lookaside_source_matches.get("file", "")
        lookaside_source_checksum = lookaside_source_matches.get("hash", "")
        lookaside_hash_algorith = lookaside_source_matches.get("alg", "md5").lower()
        lookaside_path_base: Path = Path(lookaside_source_filename)
        lookaside_path = Path.joinpath(
            lookaside_path_base,
            lookaside_hash_algorith,
            lookaside_source_checksum,
            lookaside_source_filename,
        )
        # https://<host>/repo/rpms/containernetworking-plugins/v0.8.6.tar.gz/md5/
        # 85eddf3d872418c1c9d990ab8562cc20/v0.8.6.tar.gz
        lookaside_download_url = (
            f"{settings.LOOKASIDE_CACHE_BASE_URL}/{package_type}/{package_name}/{lookaside_path}"
        )
        # eg. /tmp/lookaside/<build_id>/85eddf-v0.8.6.tar.gz
        target_filepath = Path(
            f"{settings.SCA_SCRATCH_DIR}/{LOOKASIDE_SCRATCH_SUBDIR}/{build_id}/"  # joined below
            f"{lookaside_source_checksum[:6]}-{lookaside_path_base}"
        )
        _download_source(lookaside_download_url, target_filepath)
        downloaded_sources.append(target_filepath)
    return downloaded_sources


def _download_source(download_url, target_filepath):
    package_dir = Path(target_filepath.parents[0])
    # This can be called multiple times for each source in the lookaside cache. We allow existing
    # package_dir not to fail in case this is a subsequent file we are downloading
    package_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading sources from: %s, to: %s", download_url, target_filepath)
    r: Response = requests.get(download_url)
    target_filepath.open("wb").write(r.content)


def get_tarinfo(members, archived_filename) -> Optional[tarfile.TarInfo]:
    for tarinfo in members:
        if tarinfo.name == archived_filename:
            return tarinfo
    return None

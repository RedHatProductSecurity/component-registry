import os
import re
import shutil
import subprocess  # nosec B404
import tarfile
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import django.db
import requests
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.models import Q

from config.celery import app
from corgi.collectors.brew import Brew
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
logger = get_task_logger(__name__)


def find_duplicate_component(
    component_type: str, name: str, version: str, release: str, arch: str
) -> Component:
    """Find a component with matching type, version, release, and arch but different name
    Raise an error if the mismatch isn't a known edge case"""
    logger.warning(
        f"Duplicate component {name} with type {component_type}, "
        f"version {version}, release {release}, and arch {arch} detected by Syft"
    )
    # Syft generates some purls like pkg:pypi/PyYAML@3.12
    # Happens for packages like PyYAML or PySocks with uppercase names
    # Syft also generates some purls like pkg:pypi/pyyaml@6.0
    # maybe based on the root component scanned? (e.g. casing in the specfile)
    # This probably isn't a bug in Syft, but we need names to match Brew's case
    # Sometimes Brew has lowercase and sometimes it's mixed-case
    # We can't just lowercase all names / NEVRAs because e.g. search apps need the original casing
    possible_dupes = Component.objects.filter(
        type=component_type, version=version, release=release, arch=arch
    ).filter(
        # Find a matching case-insensitive name
        # that may or may not have dashes / underscores replaced with the other
        Q(name__iexact=name)
        | Q(name__iexact=name.replace("_", "-"))
        | Q(name__iexact=name.replace("-", "_"))
    )

    if len(possible_dupes) != 1:
        # Some other case we need to consider / handle in our code
        raise ValueError(
            f"New edge case for duplicate {component_type} component: {name} "
            f"had wrong number of matches ({len(possible_dupes)})"
        )
    possible_dupe = possible_dupes[0]

    # Check if the dupe component is due to mixed-case names
    # Ignore dashes / underscores which are handled below
    old_name = possible_dupe.name.replace("-", "_")
    new_name = name.replace("-", "_")
    same_name_different_case = old_name.lower() == new_name.lower()
    same_name_different_case &= old_name != new_name

    if same_name_different_case:
        # e.g. requests-ntlm and requests-NTLM
        logger.warning(
            f"Duplicate component had case-insensitive matching name: {possible_dupe.name}"
        )

    # Ignore case differences which were handled above
    old_name = possible_dupe.name.lower()
    new_name = name.lower()
    dash_underscore_confusion = old_name.replace("-", "_") == new_name.replace("-", "_")
    dash_underscore_confusion &= old_name != new_name

    if dash_underscore_confusion:
        # e.g. requests-ntlm and requests_ntlm
        logger.warning(
            f"Duplicate component had mismatched dash or underscore: {possible_dupe.name}"
        )

    # TODO: check Parsley edge-case / error in monitoring email
    if same_name_different_case or dash_underscore_confusion:
        return possible_dupe
    else:
        # Some other case we need to consider / handle in our code
        raise ValueError(
            f"New edge case for duplicate {possible_dupe.type} component: {possible_dupe.nevra}"
        )


def save_component(
    component: dict[str, Any], parent: ComponentNode, is_go_package: bool = False
) -> bool:
    meta = component.get("meta", {})
    if component["type"] not in Component.Type.values:
        raise ValueError("Tried to save component with unknown type: %s", component["type"])

    created = False
    name = meta.pop("name", "")
    epoch = meta.pop("epoch", 0)
    version = meta.pop("version", "")
    release = meta.pop("release", "")
    arch = meta.pop("arch", "noarch")

    namespace = Brew.check_red_hat_namespace(component["type"], version)
    if component["type"] == Component.Type.GOLANG:
        # Syft doesn't support go-package detection
        # "go list" does, so we need to know which called this function
        meta["go_component_type"] = "go-package" if is_go_package else "gomod"

    elif component["type"] == Component.Type.RPM:
        # Syft doesn't set release properly in the discovered component's metadata
        if not release and "-" in version:
            version, release = version.rsplit("-", 1)

        # Syft doesn't set arch / epoch properly in the discovered component's metadata
        syft_purl = meta.get("purl", "")
        if syft_purl:
            qualifiers = syft_purl.split("?", 1)[-1].split("&")
            for qualifier in qualifiers:
                if qualifier.startswith("epoch="):
                    _, epoch = qualifier.split("=", 1)
                elif qualifier.startswith("arch="):
                    _, arch = qualifier.split("=", 1)
                # else we don't need to save any unknown qualifiers
                # because we already save the whole Syft purl in the meta_attr

    try:
        # Use all fields from Component index and uniqueness constraint
        # Don't update_or_create since Syft's metadata shouldn't override Brew's
        obj, created = Component.objects.get_or_create(
            type=component["type"],
            name=name,
            version=version,
            release=release,
            arch=arch,
            defaults={
                "epoch": epoch,
                "meta_attr": meta,
                "namespace": namespace,
            },
        )
    except django.db.IntegrityError:
        # "Get the component" fails if the name is different
        # "Create the component" fails if the purl is the same
        # Find the existing component with the same purl / different name
        # So we can add e.g. an existing PyPI package to a new container parent
        obj = find_duplicate_component(component["type"], name, version, release, arch)

    syft_purl = meta.get("purl", "")
    if syft_purl and syft_purl != obj.purl:
        # TODO: This warning appears a lot due to encoding / escaping differences
        logger.warning("Saved component purl %s, does not match Syft purl: %s", obj.purl, syft_purl)

    node, node_created = ComponentNode.objects.get_or_create(
        type=ComponentNode.ComponentNodeType.PROVIDES,
        parent=parent,
        purl=obj.purl,
        defaults={
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
def cpu_software_composition_analysis(build_uuid, force_process: bool = False):
    logger.info("Started software composition analysis for %s", build_uuid)
    software_build = SoftwareBuild.objects.get(pk=build_uuid)

    component_qs = Component.objects.filter(software_build=software_build)
    try:
        # Get root component for this build.
        root_component = component_qs.root_components().exclude(type=Component.Type.RPMMOD).get()
    except Component.DoesNotExist as exc:
        # None of the components were root components
        module_qs = component_qs.filter(type=Component.Type.RPMMOD)
        if len(module_qs) != 1:
            logger.error(f"Build {build_uuid} had wrong number of modules: {len(module_qs)}")
            # We have more than one module, or don't have any modules at all
            # so we don't know which component / don't have any component to do SCA on
            # we only do SCA on root components, so just fail the task
            raise exc

        # Else we have exactly one module component
        # which is no longer considered a "root" component
        # so now we skip doing SCA on the module instead of failing
        logger.info(
            f"Build {build_uuid} had only one module, no other root components. Skipping SCA"
        )
        return

    root_node = root_component.cnodes.first()
    if not root_node:
        raise ValueError(f"Didn't find root component node for {root_component.purl}")

    distgit_sources = _get_distgit_sources(software_build.source, build_uuid)

    no_of_new_components = _scan_files(root_node, distgit_sources)
    if no_of_new_components > 0 or force_process:
        if no_of_new_components > 0:
            logger.warning(
                f"Root component {root_component.purl} for build {build_uuid}"
                "had child components that were not found in remote-sources.json!"
            )
        app.send_task(
            "corgi.tasks.brew.slow_save_taxonomy",
            args=(software_build.build_id, software_build.build_type),
        )

    # clean up source code so that we don't have to deal with reuse and an ever growing disk
    for source in distgit_sources:
        rm_target = source
        if source.is_file():
            rm_target = source.parents[0]
        # Ignore errors because the dir might already be deleted due to multiples sources
        # being in the same directory
        shutil.rmtree(rm_target, ignore_errors=True)

    logger.info("Finished software composition analysis for %s", build_uuid)
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

    for component in detected_components:
        if save_component(component, anchor_node):
            new_components += 1
    for package in go_packages:
        if save_component(package, anchor_node, True):
            new_components += 1
    logger.info("Detected %s new components using Syft scan", new_components)
    return new_components


def _assign_go_stdlib_version(anchor_obj, go_packages):
    for go_package in go_packages:
        if (
            "version" not in go_package["meta"]
            and anchor_obj.type == Component.Type.CONTAINER_IMAGE
            and anchor_obj.arch == "noarch"
            and "go_stdlib_version" in anchor_obj.meta_attr
        ):
            go_package["meta"]["version"] = anchor_obj.meta_attr["go_stdlib_version"]


def _get_distgit_sources(source_url: str, build_uuid: str) -> list[Path]:
    distgit_sources: list[Path] = []
    raw_source, package_type, package_name = _clone_source(source_url, build_uuid)
    if not raw_source:
        return []
    distgit_sources.append(raw_source)
    sources = _download_lookaside_sources(raw_source, build_uuid, package_type, package_name)
    distgit_sources.extend(sources)
    return distgit_sources


def _clone_source(source_url: str, build_uuid: str) -> Tuple[Path, str, str]:
    # (scheme, netloc, path, parameters, query, fragment)
    url = urlparse(source_url)

    # Older builds have git, git+https, git+ssh, etc.
    # Newer builds have https
    if not url.scheme.startswith("git") and url.scheme != "https":
        raise ValueError(
            f"Build {build_uuid} had a source_url with a non-git, non-HTTPS protocol: {source_url}"
        )

    # It's an internal hostname, so we have to get it a little indirectly
    dist_git_hostname = os.environ["CORGI_LOOKASIDE_CACHE_URL"]
    dist_git_hostname = dist_git_hostname.replace("https://", "", 1)
    dist_git_hostname = dist_git_hostname.replace("/repo", "", 1)

    protocol = url.scheme
    if protocol.startswith("git+"):
        # Make git+https, git+ssh, etc. into just https, ssh, etc.
        protocol = protocol.removeprefix("git+")
    elif protocol == "git" and url.netloc == dist_git_hostname:
        # dist-git now requires us to use https when cloning
        protocol = "https"
    # Else protocol was already https or we're not using the dist-git server
    # Other protocols will raise an error above

    path = url.path
    if (
        url.netloc == dist_git_hostname
        and protocol in ("http", "https")
        and not path.startswith("/git/")
    ):
        # dist-git HTTP / HTTPS URLs require paths like /git/containers/ubi8
        # But Brew sometimes has only /containers/ubi8, which will fail
        path = f"/git{path}"
    # Else we're not using dist-git, or git+ssh became just ssh, or path already had "/git/"
    git_remote = f"{protocol}://{url.netloc}{path}"

    # Use the original path when checking length, ignore any /git/ we added
    path_parts = url.path.rsplit("/", 2)
    if len(path_parts) != 3:
        raise ValueError(f"Build {build_uuid} had a source_url with a too-short path: {source_url}")
    package_type = path_parts[1]
    package_name = path_parts[2]
    commit = url.fragment

    target_path = Path(f"{settings.SCA_SCRATCH_DIR}/{build_uuid}/")

    # Allow existing directory error to cause parent task to fail
    target_path.mkdir()

    logger.info("Fetching %s to %s", git_remote, target_path)
    try:
        subprocess.check_call(["/usr/bin/git", "clone", git_remote, target_path])  # nosec B603
    except subprocess.CalledProcessError as e:
        # There's a special case for dist-git web URLs with .git in them
        # If we aren't using dist-git, it's not a web URL, or .git isn't present, then fail
        if (
            url.netloc != dist_git_hostname
            or protocol not in ("http", "https")
            or ".git" not in url.path
        ):
            raise e

        # dist-git source URLs from Brew are sometimes incorrect, give 404
        # We don't always remove .git in case some URLs require this
        # Use the updated path with /git/ which is always needed for the clone
        path = path.replace(".git", "", 1)
        git_remote = f"{protocol}://{url.netloc}{path}"

        # Use the updated path so .git doesn't end up in package_name
        # We already know we have the right number of path_parts, based on check above
        path_parts = path.rsplit("/", 2)
        package_type = path_parts[1]
        package_name = path_parts[2]

        logger.info("Fetching %s without .git to %s", git_remote, target_path)
        subprocess.check_call(["/usr/bin/git", "clone", git_remote, target_path])  # nosec B603

    subprocess.check_call(  # nosec B603
        ["/usr/bin/git", "checkout", commit], cwd=target_path, stderr=subprocess.DEVNULL
    )
    return target_path, package_type, package_name


def _download_lookaside_sources(
    distgit_sources: Path, build_uuid: str, package_type: str, package_name: str
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
        # eg. /tmp/lookaside/<build_uuid>/85eddf-v0.8.6.tar.gz
        target_filepath = Path(
            f"{settings.SCA_SCRATCH_DIR}/{LOOKASIDE_SCRATCH_SUBDIR}/{build_uuid}/"  # joined below
            f"{lookaside_source_checksum[:6]}-{lookaside_path_base}"
        )
        _download_source(lookaside_download_url, target_filepath)
        downloaded_sources.append(target_filepath)
    return downloaded_sources


def _download_source(download_url: str, target_filepath: Path) -> None:
    package_dir = Path(target_filepath.parents[0])
    # This can be called multiple times for each source in the lookaside cache. We allow existing
    # package_dir not to fail in case this is a subsequent file we are downloading
    package_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading sources from: %s, to: %s", download_url, target_filepath)
    response = requests.get(download_url)
    if response.status_code == 404:
        # Source URLs from Brew / _clone_source / _download_lookaside_sources
        # sometimes have .git in their path, and this ends up in package_name
        # Sometimes "name.git" fails but just "name" without ".git" will work
        # e.g. both kernel-rt.git or kmod-kvdo.git work without .git suffixes
        # We need this logic, even though we sometimes strip .git above,
        # in case cloning with .git works but fetching source with .git fails
        logger.info(
            "Downloading sources from: %s without .git, to: %s", download_url, target_filepath
        )
        response = requests.get(download_url.replace(".git", "", 1))
    response.raise_for_status()
    with target_filepath.open("wb") as target_file:
        target_file.write(response.content)


def get_tarinfo(members, archived_filename) -> Optional[tarfile.TarInfo]:
    for tarinfo in members:
        if tarinfo.name == archived_filename:
            return tarinfo
    return None

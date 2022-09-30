import itertools
import json
import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Generator, Tuple
from urllib.parse import urlparse

import koji
import requests
import yaml
from django.conf import settings

from corgi.collectors.models import CollectorRhelModule, CollectorRPM, CollectorSRPM

logger = logging.getLogger(__name__)

ADVISORY_REGEX = re.compile(r"RH\wA-[12]\d{3}:\d{4,6}")


class BrewBuildTypeNotFound(Exception):
    pass


class BrewBuildTypeNotSupported(Exception):
    pass


class BrewBuildInvalidState(Exception):
    pass


class BrewBuildSourceNotFound(Exception):
    pass


class BrewBuildNotFound(Exception):
    pass


class Brew:
    """Interface to the Brew API for build data collection.

    Koji: https://docs.pagure.org/koji/
    """

    CONTAINER_BUILD_TYPE = "image"
    RPM_BUILD_TYPE = "rpm"
    MAVEN_BUILD_TYPE = "maven"
    WIN_BUILD_TYPE = "win"
    MODULE_BUILD_TYPE = "module"

    # A subset of build types that we are able to analyze right now, others from the listing
    # above will be added once support for them is added.
    SUPPORTED_BUILD_TYPES = (
        CONTAINER_BUILD_TYPE,
        RPM_BUILD_TYPE,
        MODULE_BUILD_TYPE,
    )

    # A list of component names, for which build analysis will be skipped.
    COMPONENT_EXCLUDES = json.loads(os.getenv("CORGI_COMPONENT_EXCLUDES", "[]"))

    def __init__(self):
        self.koji_session = self.get_koji_session()

    @staticmethod
    def get_koji_session():
        return koji.ClientSession(settings.BREW_URL, opts={"serverca": settings.CA_CERT})

    def get_source_of_build(self, build_info: dict) -> str:
        """Find the source used to build the Koji build."""
        no_source_msg = f'Build {build_info["id"]} has no associated source URL'
        if build_info.get("task_id") is None:
            raise BrewBuildSourceNotFound(no_source_msg)

        task_request = self.koji_session.getTaskRequest(build_info["task_id"])
        if not isinstance(task_request, list):
            raise BrewBuildSourceNotFound(no_source_msg)

        for value in task_request:
            # Check if the value in the task_request is a git URL
            if isinstance(value, str) and re.match(r"git(?:\+https?|\+ssh)?://", value):
                return value
            # Look for a dictionary in task_request that may include certain keys that hold the URL
            elif isinstance(value, dict):
                if isinstance(value.get("ksurl"), str):
                    return value["ksurl"]
                elif isinstance(value.get("indirection_template_url"), str):
                    return value["indirection_template_url"]

        raise BrewBuildSourceNotFound(no_source_msg)

    @classmethod
    def _parse_remote_source_url(cls, url: str) -> str:
        """Used to parse remote_source repo from OSBS into purl name for github namespace
        ref https://github.com/containerbuildsystem/osbs-client/blob/
        f719759af18ef9f3bb45ee4411f80a9580723e31/osbs/schemas/container.json#L310"""
        parsed_url = urlparse(url)
        path = parsed_url.path.removesuffix(".git")
        return f"{parsed_url.netloc}{path}"

    @classmethod
    def _extract_bundled_provides(cls, provides: list) -> list:
        bundled_components = []
        for component, version in provides:
            # Process bundled deps only; account for typoed golang deps of type:
            # "golang(golang.org/x/crypto/acme)"
            if component.startswith("bundled("):
                component = component.removeprefix("bundled(")
            elif component.startswith("golang("):
                pass
            else:
                # Else it's not bundled or golang, so just skip it
                continue
            # Strip right parens
            component = component.replace(")", "")
            # Split into namespace identifier and component name
            component_split = re.split(r"([(-])", component, maxsplit=1)
            if len(component_split) != 3:
                component_type = "unknown"
            else:
                component_type, separator, component = component_split

                if component_type.startswith("python"):
                    component_type = "pypi"
                elif component_type.startswith("ruby"):
                    component_type = "gem"
                elif component_type in ("npm", "nodejs", "js"):
                    component_type = "npm"
                elif component_type in ("golang", "crate"):
                    # golang and crate are both valid component types
                    pass
                else:
                    # Account for bundled deps like "bundled(rh-nodejs12-zlib)" where it's not clear
                    # what is the component type and what is the actual component name.
                    if separator == "-":
                        # E.g. unknown / rh-nodejs12-zlib
                        component = f"{component_type}-{component}"
                        component_type = "unknown"
                    else:
                        # E.g. unknown:cocoa / zlib
                        component_type = f"unknown:{component_type}"

            bundled_components.append((component_type, component, version))
        return bundled_components

    def get_rpm_build_data(self, build_id: int) -> dict:
        # Parent-level SRPM component
        srpm_component = None

        # List of child RPM components
        rpm_components = []

        # Define headers from which we'll pull extra RPM metadata
        rpm_headers = (
            "summary",
            "description",
            "license",
            "provides",
            "provideversion",
            "url",
            "source",
        )
        rpm_infos = self.koji_session.listRPMs(build_id)

        with self.koji_session.multicall() as m:
            rpm_info_header_calls = [
                (rpm_info, m.getRPMHeaders(rpmID=rpm_info["id"], headers=rpm_headers))
                for rpm_info in rpm_infos
            ]
        for rpm_info, call in rpm_info_header_calls:
            rpm_id = rpm_info["id"]
            headers = call.result
            # Create a dictionary by zipping together the values from the "provides" and
            # "provideversion" headers.
            rpm_provides = list(zip(headers.pop("provides"), headers.pop("provideversion")))

            rpm_component = {
                "type": "rpm",
                "namespace": "redhat",
                "id": rpm_id,
                "meta": {
                    "nvr": rpm_info["nvr"],
                    "name": rpm_info["name"],
                    "version": rpm_info["version"],
                    "release": rpm_info["release"],
                    "epoch": rpm_info["epoch"] or 0,  # Default to epoch 0 if not specified (`None`)
                    "arch": rpm_info["arch"],
                    **headers,
                },
                "analysis_meta": {
                    "source": ["koji.listRPMs", "koji.getRPMHeaders"],
                },
            }

            # Extract additional metadata from SRPM components
            if rpm_info["arch"] == "src":
                # TODO: download sources from dist-git, find specfile, and extract Source:
                # >>> import rpm
                # >>> t = rpm.TransactionSet()
                # >>> p = t.parseSpec('1882509_podman.spec')
                # >>> for x in p.sources:
                # ...     print(x[0])
                srpm_component = rpm_component
                continue

            # Process bundled dependencies for each RPM
            bundled_components = []
            id_generator = itertools.count(1)
            bundled_provides = self._extract_bundled_provides(rpm_provides)
            if bundled_provides:
                for component_type, bundled_component_name, version in bundled_provides:
                    bundled_component = {
                        "type": component_type,
                        "namespace": "upstream",
                        "id": f"{rpm_info['id']}-bundles-{next(id_generator)}",
                        "meta": {
                            "name": bundled_component_name,
                            "version": version,
                        },
                        "analysis_meta": {
                            "source": ["specfile"],
                        },
                    }
                    bundled_components.append(bundled_component)

            rpm_component["components"] = bundled_components
            rpm_components.append(rpm_component)

        if not srpm_component:
            logger.error("No SRPM found in build")
            return {}

        # RPM components are children of the SRPM component
        srpm_component["components"] = rpm_components

        # TODO: list all components used as build requirements
        return srpm_component

    @staticmethod
    def _build_archive_dl_url(filename: str, build_info: dict) -> str:
        url = (
            f"{settings.BREW_DOWNLOAD_ROOT_URL}/packages/"
            f"{build_info['name']}/"
            f"{build_info['version']}/"
            f"{build_info['release']}/files/remote-sources/"
            f"{filename}"
        )
        return url

    @staticmethod
    def _get_remote_source(build_archive_url: str) -> SimpleNamespace:
        response = requests.get(build_archive_url)
        response.raise_for_status()
        return json.loads(response.text, object_hook=lambda d: SimpleNamespace(**d))

    @staticmethod
    def _create_image_component(
        build_id: int,
        nvr: str,
        name: str = "",
        version: str = "",
        release: str = "",
        arch: str = "noarch",
    ) -> dict[str, Any]:
        # A multi arch image is really just an OCI image index. From a container registry client
        # point of view they are transparent in that the client will always pull the correct arch
        # for their client without having the know the actual image location.
        # See https://github.com/opencontainers/image-spec/blob/main/image-index.md
        if any(item == "" for item in (name, version, release)):
            name, version, release = Brew.split_nvr(nvr)
        return {
            "type": "container_image",
            "namespace": "redhat",
            "brew_build_id": build_id,
            "meta": {
                "nvr": nvr,
                "name": name,
                "version": version,
                "release": release,
                "arch": arch,
            },
        }

    @staticmethod
    def split_nvr(nvr):
        nvr_parts = nvr.rsplit("-", maxsplit=2)
        if len(nvr_parts) != 3:
            raise ValueError(f"NVR {nvr} had invalid length after splitting: {len(nvr_parts)}")
        name = nvr_parts[0]
        version = nvr_parts[1]
        release = nvr_parts[2]
        return name, version, release

    def get_container_build_data(self, build_id: int, build_info: dict) -> dict:

        component: dict[str, Any] = {
            "type": "image",
            "namespace": "redhat",
            "meta": {
                "name": build_info["name"],
                "version": build_info["version"],
                "release": build_info["release"],
                "epoch": build_info["epoch"] or 0,
                "arch": None,
            },
        }

        go_stdlib_version = ""
        remote_sources: dict[str, Tuple] = {}
        # TODO: Should we raise an error if build_info["extra"] is missing?
        if build_info["extra"]:
            if "index" in build_info["extra"]["image"]:
                component["meta"]["digests"] = build_info["extra"]["image"]["index"]["digests"]

            if "parent_build_id" in build_info["extra"]["image"]:
                parent_image = build_info["extra"]["image"]["parent_build_id"]
                component["meta"]["parent"] = parent_image

            # These show up in multistage builds such as Build ID 1475846 and are build dependencies
            if "parent_image_builds" in build_info["extra"]["image"]:
                build_parent_nvrs = []
                for parent_image_build in build_info["extra"]["image"][
                    "parent_image_builds"
                ].values():
                    build_name, build_version, _ = Brew.split_nvr(parent_image_build["nvr"])
                    if "go-toolset" in build_name or "golang" in build_name:
                        build_parent_nvrs.append(build_name)
                        go_stdlib_version = build_version.removeprefix("v")

                component["meta"]["build_parent_nvrs"] = build_parent_nvrs

            # Legacy OSBS builds such as 1890187 copy source code into dist-git but specify where
            # the source code came from using the 'go' stanza in container.yaml
            # ref: https://osbs.readthedocs.io/en/osbs_ocp3/users.html#go
            go_modules = []
            if "go" in build_info["extra"]["image"]:
                for module in build_info["extra"]["image"]["go"]["modules"]:
                    if "module" in module:
                        go_modules.append(module["module"])
            if go_modules:
                component["meta"]["upstream_go_modules"] = go_modules

            # builds such as 1911112 have all their info in typeinfo as they use remote_sources map
            # in remote_source json, and tar download urls by cachito url

            # Cachito ref https://osbs.readthedocs.io/en/osbs_ocp3/users.html#remote-sources
            if (
                "typeinfo" in build_info["extra"]
                and "remote-sources" in build_info["extra"]["typeinfo"]
            ):
                remote_sources_v = build_info["extra"]["typeinfo"]["remote-sources"]
                if isinstance(remote_sources_v, dict):
                    # Need to collect json, and tar download urls from archives data
                    # Fill the tuple in with empty values now
                    cachito_url = remote_sources_v["remote_source_url"]
                    remote_sources[cachito_url] = ("", "")
                else:
                    for source in remote_sources_v:
                        if "archives" in source:
                            archives = source["archives"]
                            json_data = self._build_archive_dl_url(archives[0], build_info)
                            tar = self._build_archive_dl_url(archives[1], build_info)
                            remote_sources[source["url"]] = (json_data, tar)
                        else:
                            logger.warning(
                                "Expected to find archives in remote-source dict, only found %s",
                                source.keys(),
                            )

        child_image_components: list[dict[str, Any]] = []
        archives = self.koji_session.listArchives(build_id)

        # Extract the list of embedded rpms
        noarch_rpms_by_id: dict[int, dict[str, Any]] = {}
        rpm_build_ids: set[int] = set()

        for archive in archives:
            if archive["btype"] == "image" and archive["type_name"] == "tar":
                noarch_rpms_by_id, child_image_component = self._extract_image_components(
                    archive, build_id, build_info["nvr"], noarch_rpms_by_id, rpm_build_ids
                )
                child_image_components.append(child_image_component)
            if archive["btype"] == "remote-sources":
                # Some OSBS builds don't have remote sources set in extras typeinfo
                # For example build 1475846 because they use remote_source in Cachito Configuration
                # ref: https://osbs.readthedocs.io/en/osbs_ocp3/users.html#remote-source
                # In that case, extract from archives data here
                if len(remote_sources.keys()) == 1:
                    first_remote_source = next(iter(remote_sources.values()))
                    # The remote_source tuple is updated during the loop below, so we need to check
                    # if both json and tar values in the tuple are empty
                    if first_remote_source[0] != "" and first_remote_source[1] != "":
                        continue  # Don't try to populate remote_sources map from archives
                    else:
                        self.update_remote_sources(archive, build_info, remote_sources)

        source_components = self._extract_remote_sources(go_stdlib_version, remote_sources)

        component["nested_builds"] = list(rpm_build_ids)
        component["sources"] = source_components
        component["image_components"] = child_image_components
        component["components"] = noarch_rpms_by_id.values()
        return component

    def _extract_remote_sources(self, go_stdlib_version, remote_sources):
        source_components: list[dict[str, Any]] = []
        for build_loc, coords in remote_sources.items():
            remote_source = self._get_remote_source(coords[0])
            source_component: dict[str, Any] = {
                "type": "upstream",
                "namespace": "redhat",
                "meta": {
                    "name": self._parse_remote_source_url(remote_source.repo),
                    "version": remote_source.ref,
                    "remote_source": coords[0],
                    "remote_source_archive": coords[1],
                },
                "analysis_meta": {"source": ["koji.listArchives"]},
            }
            if build_loc:
                source_component["meta"]["cachito_build"] = build_loc
            logger.info(
                "Processing archive %s with package managers: %s",
                coords[0],
                remote_source.pkg_managers,
            )
            for pkg_type in remote_source.pkg_managers:
                if pkg_type in ("npm", "pip", "yarn"):
                    provides, remote_source.packages = self._extract_provides(
                        remote_source.packages, pkg_type
                    )
                    try:
                        source_component["components"].extend(provides)
                    except KeyError:
                        source_component["components"] = provides
                elif pkg_type == "gomod":
                    (
                        source_component["components"],
                        remote_source.packages,
                    ) = self._extract_golang(remote_source.packages, go_stdlib_version)
                    (
                        source_component["components"],
                        remote_source.dependencies,
                    ) = self._extract_golang(remote_source.dependencies, go_stdlib_version)
                else:
                    logger.warning("Found unsupported remote-source pkg_manager %s", pkg_type)
            source_components.append(source_component)
        return source_components

    def update_remote_sources(self, archive, build_info, remote_sources):
        cachito_url = next(iter(remote_sources))
        logger.debug("Setting remote sources for %s using archive data %s", cachito_url, archive)
        remote_sources_url = self._build_archive_dl_url(archive["filename"], build_info)
        # Update the remote sources download url tuple
        existing_coords = list(remote_sources[cachito_url])
        if archive["type_name"] == "tar":
            remote_sources[cachito_url] = tuple([existing_coords[0], remote_sources_url])
        elif archive["type_name"] == "json":
            remote_sources[cachito_url] = tuple([remote_sources_url, existing_coords[1]])

    @staticmethod
    def extract_common_key(filename):
        without_prefix = filename.removeprefix("remote-source-")
        return without_prefix.split(".", 1)[0]

    def _extract_image_components(
        self,
        archive: dict[str, Any],
        build_id: int,
        build_nvr: str,
        noarch_rpms_by_id: dict[int, dict[str, Any]],
        rpm_build_ids: set[int],
    ) -> Tuple[dict[int, dict[str, Any]], dict[str, Any]]:
        logger.info("Processing image archive %s", archive["filename"])
        child_component = self._create_image_component(
            build_id, build_nvr, arch=archive["extra"]["image"]["arch"]
        )
        child_component["meta"]["docker_config"] = archive["extra"]["docker"]["config"]
        child_component["meta"]["brew_archive_id"] = archive["id"]
        child_component["meta"]["digests"] = archive["extra"]["docker"]["digests"]
        child_component["analysis_meta"] = {"source": ["koji.listArchives"]}
        rpms = self.koji_session.listRPMs(imageID=archive["id"])
        arch_specific_rpms = []
        for rpm in rpms:
            rpm_component = {
                "type": "rpm",
                "namespace": "redhat",
                "brew_build_id": rpm["build_id"],
                "meta": {
                    "nvr": rpm["nvr"],
                    "name": rpm["name"],
                    "version": rpm["version"],
                    "release": rpm["release"],
                    "arch": rpm["arch"],
                    "rpm_id": rpm["id"],
                },
                "analysis_meta": {"source": "koji.listRPMs"},
            }
            rpm_build_ids.add(rpm["build_id"])
            if rpm["arch"] == "noarch":
                noarch_rpms_by_id[rpm["id"]] = rpm_component
            else:
                arch_specific_rpms.append(rpm_component)
        child_component["rpm_components"] = arch_specific_rpms
        return noarch_rpms_by_id, child_component

    def _extract_provides(
        self, packages: list[SimpleNamespace], pkg_type: str
    ) -> Tuple[list[dict[str, Any]], list[SimpleNamespace]]:
        components: list[dict[str, Any]] = []
        typed_pkgs, remaining_packages = self._filter_by_type(packages, pkg_type)
        for typed_pkg in typed_pkgs:
            typed_component: dict[str, Any] = {
                "type": pkg_type,
                "meta": {
                    "name": typed_pkg.name,
                    "version": typed_pkg.version,
                },
            }
            try:
                typed_component["meta"]["path"] = typed_pkg.path
            except AttributeError:
                pass

            typed_component["components"] = []
            for dep in typed_pkg.dependencies:
                component = {
                    "type": dep.type,
                    "meta": {
                        "name": dep.name,
                        "version": dep.version,
                    },
                }
                # The dev key is only present for Cachito package managers which support
                # dev dependencies. See https://github.com/containerbuildsystem/cachito/blob/
                # f3e954e3d04d2cd35cc878c1189cd55e7471220d/docs/metadata.md#dependencydev
                if hasattr(dep, "dev"):
                    component["meta"]["dev"] = dep.dev
                typed_component["components"].append(component)
            components.append(typed_component)
        return components, remaining_packages

    def _extract_golang(
        self, dependencies: list[SimpleNamespace], go_stdlib_version: str = ""
    ) -> Tuple[list[dict[str, Any]], list[SimpleNamespace]]:
        dependants: list[dict[str, Any]] = []
        modules, remaining_deps = self._filter_by_type(dependencies, "gomod")
        packages, remaining_deps = self._filter_by_type(remaining_deps, "go-package")
        # Golang packages are related to modules by name.
        module_packages: dict[tuple, list[dict[str, Any]]] = {}
        # Add all the modules directly to the source.
        for module in modules:
            module_packages[module.name, module.version] = []

        # Nest packages under the module they belong to
        for pkg in packages:
            found_matching_module = False
            pkg_name = pkg.name.removeprefix("vendor/")
            # This indicates it's a stdlib component, and get its version from the golang compiler
            if not pkg.version:
                pkg.version = go_stdlib_version
            for module in modules:
                if pkg_name.startswith(module.name):
                    found_matching_module = True
                    dependant_provides: dict[str, Any] = {
                        "type": "go-package",
                        "meta": {
                            "name": pkg_name,
                            "version": pkg.version,
                        },
                    }
                    module_packages[module.name, module.version].append(dependant_provides)
                    break  # from iterating modules
            if not found_matching_module:
                # Add this package as a direct dependency of the source
                # Usually these are golang standard library packages
                direct_dependant: dict[str, Any] = {
                    "type": "go-package",
                    "meta": {
                        "name": pkg.name,
                        "version": pkg.version,
                    },
                }
                dependants.append(direct_dependant)

        # Add modules with nested packages
        module_version: tuple
        package_list: list[dict[str, Any]]
        for module_version, package_list in module_packages.items():
            dependant: dict[str, Any] = {
                "type": "gomod",
                "meta": {"name": module_version[0], "version": module_version[1]},
            }
            if len(package_list) > 0:
                dependant["components"] = package_list
            dependants.append(dependant)
        return dependants, remaining_deps

    @staticmethod
    def _filter_by_type(
        dependencies: list[SimpleNamespace], pkg_type: str
    ) -> Tuple[list[SimpleNamespace], list[SimpleNamespace]]:
        filtered: list[SimpleNamespace] = []
        remaining_deps = dependencies[:]
        for dep in dependencies:
            if dep.type == pkg_type:
                filtered.append(dep)
                remaining_deps.remove(dep)
        return filtered, remaining_deps

    @staticmethod
    def get_maven_build_data(build_info: dict, build_type_info: dict) -> dict:
        component = {
            "type": "maven",
            "namespace": "redhat",
            "meta": {
                # Strip release since it's not technically part of the unique GAV identifier
                "gav": build_info["nvr"].rsplit("-", maxsplit=1)[0],
                "group_id": build_type_info["maven"]["group_id"],
                "artifact_id": build_type_info["maven"]["artifact_id"],
                "version": build_type_info["maven"]["version"],
            },
        }
        # TODO: add more info
        return component

    @classmethod
    def _extract_advisory_ids(cls, build_tags: list) -> list:
        advisory_ids = set()
        for tag in build_tags:
            match = ADVISORY_REGEX.match(tag)
            if match:
                advisory_ids.add(match.group())
        return list(advisory_ids)

    @staticmethod
    def get_module_build_data(build_info: dict) -> dict:

        modulemd_yaml = build_info["extra"]["typeinfo"]["module"].get("modulemd_str", "")
        if not modulemd_yaml:
            raise ValueError("Cannot get module build data, modulemd_yaml is undefined")
        modulemd = yaml.safe_load(modulemd_yaml)
        meta_attr = {
            "stream": modulemd["data"]["stream"],
            "context": modulemd["data"]["context"],
            "components": modulemd["data"].get("components", []),
            "rpms": modulemd["data"]["xmd"]["mbs"]["rpms"],
        }
        module = {
            "type": "module",
            "namespace": "redhat",
            "meta": {
                "name": build_info["name"],
                "version": build_info["version"],
                "license": " OR ".join(modulemd["data"]["license"].get("module", "")),
                "release": build_info["release"],
                "description": modulemd["data"]["description"],
                "meta_attr": meta_attr,
            },
            "analysis_meta": {
                "source": ["koji.getBuild"],
            },
        }

        return module

    def get_component_data(self, build_id: int) -> dict:
        logger.info("Retrieving Brew build: %s", build_id)
        build = self.koji_session.getBuild(build_id)
        if not build:
            raise BrewBuildNotFound(f"Build {build_id} was not found")

        # Determine build state
        state = build.get("state")
        if state != koji.BUILD_STATES["COMPLETE"]:
            raise BrewBuildInvalidState(f"Build {build_id} state is {state}; skipping!")

        if build["name"] in self.COMPONENT_EXCLUDES:
            logger.info(f"Skipping processing build {build_id} ({build['name']})")
            return {}

        # Determine build type
        build_type_info = self.koji_session.getBuildType(build)
        build_type = next(
            (type_ for type_ in build_type_info.keys() if type_ in self.SUPPORTED_BUILD_TYPES),
            "unknown",
        )
        if not any(type_ in self.SUPPORTED_BUILD_TYPES for type_ in build_type_info.keys()):
            raise BrewBuildTypeNotSupported(
                f"Build {build_id} type is not supported: {build_type_info}"
            )
        # TODO: refactor Brew.CONTAINER_BUILD_TYPE to be a generic IMAGE_TYPE with image types
        #  identified in a separate attribute on the build itself.
        if build_type == self.CONTAINER_BUILD_TYPE:
            # If this is an "image" type build, it may be building a container image, ISO image,
            # or other types of images. Check the content generator name to determine where this
            # image was built, which indicates what type of image it is.
            if build["cg_name"] != "atomic-reactor":
                # Container images are built in OSBS, which uses atomic-reactor to build them.
                raise BrewBuildTypeNotSupported(
                    f"Image build {build_id} is not supported: "
                    f"{build['cg_name']} content generator used"
                )
        build["type"] = build_type

        # Determine build source
        if not build.get("source"):
            # Sometimes there is no source URL on the build, but it can be found in the task
            # request info instead.
            logger.info("Fetching source from task info for %s", build_id)
            try:
                build["source"] = self.get_source_of_build(build)
            except BrewBuildSourceNotFound as exc:
                # Some older builds do not specify source URLs; the below date was chosen based
                # on some initial analysis of source-less builds in Brew.
                if datetime.fromtimestamp(build["completion_ts"]) < datetime(2015, 1, 1):
                    logger.error(
                        f"Build {build_id} has no associated source URL but is too old "
                        f"to process; returning an empty component."
                    )
                    return {}
                else:
                    raise exc

        # Add list of Brew tags for this build
        tags = self.koji_session.listTags(build_id)
        build["tags"] = [tag["name"] for tag in tags]
        build["errata_tags"] = self._extract_advisory_ids(build["tags"])

        # TODO: handle wrapper RPM builds:
        # brew buildID=1839210
        # These should create the necessary RPM components and then hand off the rest of the
        # analysis to the maven analyzer to actually map the jars shipped within the RPMs.

        # Add additional data based on the build type
        if build_type == self.CONTAINER_BUILD_TYPE:
            component = self.get_container_build_data(build_id, build)
        elif build_type == self.RPM_BUILD_TYPE:
            component = self.get_rpm_build_data(build_id)
        elif build_type == self.MODULE_BUILD_TYPE:
            component = self.get_module_build_data(build)
        else:
            raise BrewBuildTypeNotSupported(
                f"Build {build_id} of type {build_type} is not supported"
            )

        component["build_meta"] = {"build_info": build, "type_info": build_type_info}
        return component

    def get_builds_with_tag(
        self, brew_tag: str, inherit: bool = False, latest: bool = True
    ) -> tuple:
        try:
            builds = self.koji_session.listTagged(brew_tag, inherit=inherit, latest=latest)
            return tuple(b["build_id"] for b in builds)
        except koji.GenericError as exc:
            logger.warning("Couldn't find brew builds with tag %s: %s", brew_tag, exc)
            return tuple()

    def brew_srpm_lookup(self, srpms) -> tuple:
        """The Koji API findBuild call can except NVR as a format"""
        with self.koji_session.multicall() as multicall:
            find_build_id_calls = tuple((srpm, multicall.findBuildID(srpm)) for srpm in srpms)
        return find_build_id_calls

    def brew_rpm_lookup(self, rpms) -> tuple:
        """The Koji API getRPM call can except rpm in NVR"""
        with self.koji_session.multicall() as multicall:
            find_build_id_calls = tuple((rpm, multicall.getRPM(rpm)) for rpm in rpms)
        return find_build_id_calls

    def sans_epoch(self, rpm) -> str:
        """This removed the epoch part of a SRPM or RPM so the RPM name is in NVR format"""
        name, version, release = self.split_nvr(rpm)
        version_parts = version.split(":")
        if len(version_parts) > 1:
            rpm = f"{name}-{version_parts[1]}-{release}"
        return rpm

    def module_key_to_nvr(self, module_key) -> str:
        """This adjusts the rhel_module name found in composes to be in NVR format expected by
        the Koji API"""
        module_parts = module_key.split(":")
        return f"{module_parts[0]}-{module_parts[1]}-{module_parts[2]}.{module_parts[3]}"

    def persist_modules(self, rhel_modules) -> Generator[str, None, None]:
        # For each rhel_module look up it's build_id
        find_build_id_calls = self.brew_srpm_lookup(rhel_modules.keys())
        for srpm, call in find_build_id_calls:
            build_id = call.result
            if not build_id:
                logger.warning("Did not find build_id for rhel_module: %s", srpm)
                continue
            rhel_module, _ = CollectorRhelModule.objects.get_or_create(
                build_id=build_id,
                defaults={"nvr": srpm},
            )
            # Lookup the rpm build_ids
            rpms = [self.sans_epoch(rpm) for rpm in rhel_modules[srpm] if not rpm.endswith(".src")]
            rpm_lookup_calls = self.brew_rpm_lookup(rpms)
            for rpm, call in rpm_lookup_calls:
                srpm_build_id = call.result["build_id"]
                srpm, _ = CollectorSRPM.objects.get_or_create(build_id=srpm_build_id)
                rpm_obj, _ = CollectorRPM.objects.get_or_create(nvra=rpm, srpm=srpm)
                rpm_obj.rhel_module.add(rhel_module)

            yield build_id

    def lookup_build_ids(self, rpm_filenames_by_srpm) -> Generator[str, None, None]:
        # For each srpm look up it's build id
        find_build_id_calls = self.brew_srpm_lookup(rpm_filenames_by_srpm.keys())
        for srpm, call in find_build_id_calls:
            build_id = call.result
            if not build_id:
                for filename in rpm_filenames_by_srpm[srpm]:
                    logger.debug(
                        "Didn't find build with NVR %s, using rpm filename: %s",
                        srpm,
                        filename,
                    )
                    # We don't use a multicall here, because this won't be called
                    # in most cases
                    rpm_data = self.koji_session.getRPM(filename)
                    if not rpm_data:
                        # Try the next srpm rpm filename
                        continue
                    build_id = rpm_data["build_id"]
                    # found the build_id, stop iterating filenames
                    break
                # if no filenames had RPM data
                if not build_id:
                    logger.warning("Unable to find build_id for %s", srpm)
                    continue
            yield build_id

    @classmethod
    def fetch_rhel_module(cls, build_id: int) -> dict[str, Any]:
        try:
            rhel_module = CollectorRhelModule.objects.get(build_id=build_id)
        except CollectorRhelModule.DoesNotExist:
            logger.debug("Did not find %s in CollectorRhelModule data", build_id)
            return {}
        name, version, release = Brew.split_nvr(rhel_module.nvr)
        module: dict[str, Any] = {
            "type": "module",
            "namespace": "redhat",
            "meta": {
                "name": name,
                "version": version,
                "release": release,
            },
            "analysis_meta": {
                "source": ["collectors/rhel_module"],
            },
        }
        nested_builds: set[int] = set()
        rpm_components: list[dict] = []
        for rpm in rhel_module.collectorrpm_set.all():
            srpm_build_id = rpm.srpm.build_id
            nested_builds.add(srpm_build_id)
            name, version, release = Brew.split_nvr(rpm.nvra)
            release_split = release.rsplit(".", 1)
            arch = ""
            if len(release_split) == 2:
                arch = release_split[1]
            rpm_component: dict = {
                "type": "rpm",
                "namespace": "redhat",
                "brew_build_id": srpm_build_id,
                "meta": {
                    "name": name,
                    "version": version,
                    "release": release_split[0],
                    "arch": arch,
                },
                "analysis_meta": {"source": "collectors/rhel_module"},
            }
            rpm_components.append(rpm_component)
        module["components"] = rpm_components
        module["nested_builds"] = list(nested_builds)
        return module

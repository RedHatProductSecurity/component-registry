import itertools
import json
import logging
import re
from types import SimpleNamespace
from typing import Any, Tuple
from urllib.parse import urlparse

import koji  # type: ignore
import requests
from django.conf import settings

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


class Brew:
    """Interface to the Brew API for build data collection.

    Koji: https://docs.pagure.org/koji/
    """

    CONTAINER_BUILD_TYPE = "image"
    RPM_BUILD_TYPE = "rpm"
    MAVEN_BUILD_TYPE = "maven"
    WIN_BUILD_TYPE = "win"
    MODULE_BUILD_TYPE = "module"
    SUPPORTED_BUILD_TYPES = (
        CONTAINER_BUILD_TYPE,
        RPM_BUILD_TYPE,
        # These builds fail because we don't support Maven (yet?)
        # MAVEN_BUILD_TYPE,
        WIN_BUILD_TYPE,
        # These builds fail because we don't support RHEL modules
        # MODULE_BUILD_TYPE,
    )

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
        if task_request is None:
            raise BrewBuildSourceNotFound(no_source_msg)
        elif not isinstance(task_request, list):
            raise BrewBuildSourceNotFound(no_source_msg)

        for value in task_request:
            # Check if the value in the task_request is a git URL
            if isinstance(value, str) and re.match(r"git(\+https?|\+ssh)?://", value):
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

            rpm_deps = self.get_koji_session().getRPMDeps(rpm_id)
            rpm_component["meta"]["capabilities"] = self._extract_rpm_capabilities(rpm_id, rpm_deps)
            rpm_component["components"] = bundled_components
            rpm_components.append(rpm_component)

        if not srpm_component:
            logger.error("No SRPM found in build")
            return {}

        # RPM components are children of the SRPM component
        srpm_component["components"] = rpm_components

        # TODO: list all components used as build requirements
        return srpm_component

    @classmethod
    def _extract_rpm_capabilities(cls, rpm_id: int, rpm_caps: list) -> list:
        if not rpm_caps:
            return []

        capabilities = []
        id_generator = itertools.count(1)
        for cap in rpm_caps:
            # Store required, provides, and recommended types
            # Ref: https://pagure.io/koji/blob/master/f/koji/__init__.py#_225
            #      https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/8/html/packaging_and_distributing_software/new-features-in-rhel-8_packaging-and-distributing-software
            if cap["type"] == koji.DEP_REQUIRE:
                relationship = "requires"
            elif cap["type"] == koji.DEP_PROVIDE:
                relationship = "provides"
            elif cap["type"] == koji.DEP_RECOMMEND:
                relationship = "recommends"
            else:
                continue
            # RPM capabilities are strings the Package Manager (DNF) uses to determine relationships
            #  https://docs.fedoraproject.org/en-US/Fedora_Draft_Documentation/0.1/html/RPM_Guide/ch-dependencies.html#RPM_Guide-Dependencies-Understanding
            capability = {
                "type": relationship,
                "id": f"{rpm_id}-cap-{next(id_generator)}",
                "name": cap["name"],
                "version": cap["version"],
                # Use kojiweb.utils to translate when displaying
                # https://pagure.io/koji/blob/master/f/www/lib/kojiweb/util.py#_482
                "flags": cap["flags"],
                "analysis_meta": {
                    "source": ["koji.getRPMDeps"],
                },
            }
            capabilities.append(capability)
        return capabilities

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
        if any(item == "" for item in [name, version, release]):
            name, release, version = Brew.split_nvr(nvr)
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
        if len(nvr_parts) == 3:
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

        if "index" in build_info["extra"]["image"]:
            component["meta"]["digests"] = build_info["extra"]["image"]["index"]["digests"]

        if "parent_build_id" in build_info["extra"]["image"]:
            parent_image = build_info["extra"]["image"]["parent_build_id"]
            component["meta"]["parent"] = parent_image

        go_stdlib_version = ""
        # These show up in multi-stage builds such as Build ID 1475846 and are build dependencies
        if "parent_image_builds" in build_info["extra"]["image"]:
            build_parent_nvrs = []
            for parent_image_build in build_info["extra"]["image"]["parent_image_builds"].values():
                build_name, build_version, _ = Brew.split_nvr(parent_image_build["nvr"])
                if "go-toolset" in build_name or "golang" in build_name:
                    build_parent_nvrs.append(build_name)
                    go_stdlib_version = build_version.removeprefix("v")

            component["meta"]["build_parent_nvrs"] = build_parent_nvrs

        source_components: list[dict[str, Any]] = []
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
            elif archive["btype"] == "remote-sources" and archive["type_name"] == "json":
                try:
                    url = self._build_archive_dl_url(archive["filename"], build_info)
                    remote_source = self._get_remote_source(url)
                except requests.HTTPError:
                    logger.warning(
                        "Got HTTPError trying to retrieve remote sources for %s/%s",
                        build_id,
                        archive["filename"],
                    )
                    continue  # skip this archive
                source_component: dict[str, Any] = {
                    "type": "upstream",
                    "namespace": "redhat",
                    "meta": {
                        "remote_source": archive["filename"],
                        "name": self._parse_remote_source_url(remote_source.repo),
                        "version": remote_source.ref,
                    },
                    "analysis_meta": {"source": ["koji.listArchives"]},
                }
                logger.info(
                    "Processing archive %s with package managers: %s",
                    archive["filename"],
                    remote_source.pkg_managers,
                )
                for pkg_type in remote_source.pkg_managers:
                    if pkg_type in ["npm", "pip", "yarn"]:
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
            component["nested_builds"] = list(rpm_build_ids)
            component["sources"] = source_components
            component["image_components"] = child_image_components

        # TODO this might be an old OSBS build, need to download manifest from OSBS directly

        component["components"] = noarch_rpms_by_id.values()
        return component

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

            typed_component["build_components"] = []
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

    def get_maven_build_data(self, build_id: int, build_info: dict, build_type_info: dict) -> dict:
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

    def get_component_data(self, build_id: int) -> dict:
        logger.info("Retrieving Brew build: %s", build_id)
        build = self.koji_session.getBuild(build_id)

        # Determine build state
        state = build.get("state")
        if state != koji.BUILD_STATES["COMPLETE"]:
            raise BrewBuildInvalidState(f"Build {build_id} state is {state}; skipping!")

        # Determine build type
        build_type_info = self.koji_session.getBuildType(build)
        if not any(type_ in self.SUPPORTED_BUILD_TYPES for type_ in build_type_info.keys()):
            raise BrewBuildTypeNotSupported(
                f"Build {build_id} type is not supported: {build_type_info}"
            )
        build_type = next(
            type_ for type_ in build_type_info.keys() if type_ in self.SUPPORTED_BUILD_TYPES
        )
        build["type"] = build_type

        # Determine build source
        if not build.get("source"):
            # Sometimes there is no source URL on the build but it can be found in the task
            # request info instead.
            logger.info("Fetching source from task info for %s", build_id)
            build["source"] = self.get_source_of_build(build)

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
        elif build_type == self.MAVEN_BUILD_TYPE:
            component = self.get_maven_build_data(build_id, build, build_type_info)
        elif build_type == self.MODULE_BUILD_TYPE:
            component = {}
        elif build_type == self.WIN_BUILD_TYPE:
            component = {}
        else:
            component = {}

        component["build_meta"] = {"build_info": build, "type_info": build_type_info}
        return component

    def get_builds_with_tag(self, brew_tag: str, inherit: bool = False) -> list[int]:
        brew = self.get_koji_session()
        try:
            builds = brew.listTagged(brew_tag, inherit=inherit)
            return [b["build_id"] for b in builds]
        except koji.GenericError as exc:
            logger.warning("Couldn't find brew builds with tag %s: %s", brew_tag, exc)
        return []

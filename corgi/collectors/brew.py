import json
import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Generator, Iterable, Optional, Union
from urllib.parse import urlparse

import koji
import requests
import yaml
from django.conf import settings

from config.celery import app
from corgi.collectors.models import CollectorRhelModule, CollectorRPM, CollectorSRPM
from corgi.core.constants import CONTAINER_REPOSITORY
from corgi.core.models import Component, SoftwareBuild

logger = logging.getLogger(__name__)

ADVISORY_REGEX = re.compile(r"RH[BES]A-[12]\d{3}:\d{4,}")


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

    CARGO_TYPE_MAPPING = {"crate": Component.Type.CARGO}
    GEM_TYPE_MAPPING = {"ruby": Component.Type.GEM, "rubygems": Component.Type.GEM}
    GOLANG_TYPE_MAPPING = {
        "golang": Component.Type.GOLANG,
        "gomod": Component.Type.GOLANG,
        "go-package": Component.Type.GOLANG,
    }
    NPM_TYPE_MAPPING = {
        "js": Component.Type.NPM,
        "nodejs": Component.Type.NPM,
        "npm": Component.Type.NPM,
        "yarn": Component.Type.NPM,
    }
    PYPI_TYPE_MAPPING = {"pip": Component.Type.PYPI, "python": Component.Type.PYPI}

    # Map Cachito types to Corgi types
    CACHITO_PKG_TYPE_MAPPING = {
        **CARGO_TYPE_MAPPING,
        **GEM_TYPE_MAPPING,
        **GOLANG_TYPE_MAPPING,
        **NPM_TYPE_MAPPING,
        **PYPI_TYPE_MAPPING,
    }

    # A list of component names, for which build analysis will be skipped.
    COMPONENT_EXCLUDES = json.loads(os.getenv("CORGI_COMPONENT_EXCLUDES", "[]"))

    KOJI_LISTARCHIVES_SRC = "koji.listArchives"
    RHCOS_BUILDER = "coreos-assembler"

    koji_session: koji.ClientSession = None

    def __init__(self, source: str = ""):
        if source == SoftwareBuild.Type.CENTOS:
            self.koji_session = koji.ClientSession(settings.CENTOS_URL)
        elif source == SoftwareBuild.Type.KOJI:
            self.koji_session = koji.ClientSession(settings.BREW_URL)
        elif source == SoftwareBuild.Type.BREW:
            self.koji_session = koji.ClientSession(
                settings.BREW_URL, opts={"serverca": settings.CA_CERT}
            )
        else:
            raise ValueError(f"Tried to create Brew collector with invalid type: {source}")

    def get_source_of_build(self, build_info: dict[str, Any]) -> str:
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

    @staticmethod
    def clean_source_of_build(source_url: str) -> str:
        """Handle old builds which still use git:// URLs
        The dist-git service now requires us to use https:// instead"""
        # It's an internal hostname, so we have to get it a little indirectly
        dist_git_hostname = os.environ["CORGI_LOOKASIDE_CACHE_URL"]
        dist_git_hostname = dist_git_hostname.replace("https://", "", 1)
        dist_git_hostname = dist_git_hostname.replace("/repo", "", 1)

        # Find any build where the source URL starts with git:// or similar
        gitlike_urls = (
            f"git://{dist_git_hostname}/",
            f"git+http://{dist_git_hostname}/",
            f"git+https://{dist_git_hostname}/",
            f"git+ssh://{dist_git_hostname}/",
        )
        # Replace it with https:// and add "/git/" if not already present
        dist_git_url = f"https://{dist_git_hostname}/git/"

        for git_url in gitlike_urls:
            git_url_with_path = f"{git_url}git/"

            # If "/git/" is already present, just fix the scheme
            if source_url.startswith(git_url_with_path):
                source_url = source_url.replace(git_url_with_path, dist_git_url, 1)
                break

            # If "/git/" isn't already present, fix the scheme and path
            elif source_url.startswith(git_url):
                source_url = source_url.replace(git_url, dist_git_url, 1)
                break

        # Handle .git suffix which must be removed in HTTPS URLs
        source_url = source_url.replace(".git", "", 1)
        return source_url

    @staticmethod
    def _parse_remote_source_url(url: str) -> tuple[str, Component.Type]:
        """Used to parse remote_source repo from OSBS into purl name for github namespace
        ref https://github.com/containerbuildsystem/osbs-client/blob/
        f719759af18ef9f3bb45ee4411f80a9580723e31/osbs/schemas/container.json#L310"""
        parsed_url = urlparse(url)
        path = parsed_url.path.removesuffix(".git")

        # handle url like git@github.com:rh-gitops-midstream/argo-cd
        if path.startswith("git@"):
            path = path.removeprefix("git@")
            path = path.replace(":", "/")

        # look for github.com and set ComponentType with modified path
        if parsed_url.netloc == "github.com":
            component_type = Component.Type.GITHUB
            # urlparse keeps the leading / on the path component when netloc was found
            # the purl spec dictates that we remove it for Github purls
            path = path.removeprefix("/")

        # no netloc
        elif path.startswith("github.com/"):
            component_type = Component.Type.GITHUB
            path = path.removeprefix("github.com/")

        # non github url with netloc
        else:
            component_type = Component.Type.GENERIC
            path = f"{parsed_url.netloc}{path}"

        return path, component_type

    @staticmethod
    def _bundled_or_golang(component: str) -> str:
        # Process bundled deps only; account for typed golang deps of type:
        # "golang(golang.org/x/crypto/acme)"
        if component.startswith("bundled("):
            c = component.removeprefix("bundled(")
        elif component.startswith("golang("):
            c = component
        else:
            return ""
        # Strip right parens, even if we didn't strip prefix
        return c.replace(")", "")

    @staticmethod
    def _check_maven_component(
        component: str, version: str
    ) -> Optional[tuple[Component.Type, str, str]]:
        if ":" in component:
            return Component.Type.MAVEN, component.replace(":", "/"), version
        elif component.startswith("maven"):
            return Component.Type.MAVEN, component, version
        elif component.startswith("apache-commons"):
            return Component.Type.MAVEN, component, version
        elif component.startswith("java-"):
            return Component.Type.MAVEN, component, version
        return None

    @staticmethod
    def _check_npm_component(
        component: str, version: str
    ) -> Optional[tuple[Component.Type, str, str]]:
        if component.startswith("js-"):
            return Component.Type.NPM, component[len("js-") :], version
        elif component.startswith("npm-"):
            return Component.Type.NPM, component[len("npm-") :], version
        elif component.startswith("nodejs-"):
            return Component.Type.NPM, component[len("nodejs-") :], version
        else:
            component_match = re.match(r"^nodejs\d+-(.*)", component)
            if component_match:
                return Component.Type.NPM, component, version
        return None

    @classmethod
    def _get_bundled_component_type(
        cls, component_type: str, component: str
    ) -> Optional[Component.Type]:
        if component_type.startswith("python"):
            return Component.Type.PYPI
        elif component_type.startswith("ruby"):
            return Component.Type.GEM
        elif component_type == "golang":
            # Need to skip arch names, See CORGI-48
            if component in ("aarch-64", "ppc-64", "s390-64", "x86-64"):
                return None
            return Component.Type.GOLANG
        elif component_type in cls.CACHITO_PKG_TYPE_MAPPING:
            return cls.CACHITO_PKG_TYPE_MAPPING[component_type]
        else:
            return Component.Type.GENERIC

    @classmethod
    def _extract_bundled_provides(
        cls, provides: list[tuple[str, str]]
    ) -> list[tuple[Component.Type, str, str]]:
        bundled_components: list[tuple[Component.Type, str, str]] = []
        for component, version in provides:
            component = cls._bundled_or_golang(component)
            if not component:
                continue
            if component.startswith("rh-"):
                component = component[3:]
            bundled_component = cls._check_maven_component(component, version)
            if bundled_component:
                bundled_components.append(bundled_component)
                continue
            bundled_component = cls._check_npm_component(component, version)
            if bundled_component:
                bundled_components.append(bundled_component)
                continue
            # Split into namespace identifier and component name
            component_split = re.split(r"([(-])", component, maxsplit=1)
            if len(component_split) != 3:
                bundled_components.append((Component.Type.GENERIC, component, version))
                continue
            else:
                component_type, seperator, component = component_split
                bundled_component_type = cls._get_bundled_component_type(component_type, component)
                if not bundled_component_type:
                    continue
                # Account for bundled deps like "bundled(rh-nodejs12-zlib)" where it's not clear
                # what is the component type and what is the actual component name.
                if bundled_component_type == Component.Type.GENERIC and seperator == "-":
                    # E.g. unknown / rh-nodejs12-zlib
                    component = f"{component_type}-{component}"
            bundled_components.append((bundled_component_type, component, version))
        return bundled_components

    def get_rpm_build_data(self, build_id: int) -> dict[str, Any]:
        # Parent-level SRPM component
        srpm_component = None

        # List of child RPM components
        rpm_components = []

        rpm_infos = self.koji_session.listRPMs(build_id)

        for rpm_info, call in self.brew_rpm_headers_lookup(rpm_infos):
            rpm_id = rpm_info["id"]
            headers = call.result

            # Create a dictionary by zipping together the values from the "provides" and
            # "provideversion" headers.
            rpm_provides = list(zip(headers.pop("provides"), headers.pop("provideversion")))
            rpm_component: dict[str, Any] = {
                "type": Component.Type.RPM,
                "namespace": Component.Namespace.REDHAT,
                "meta": {
                    **headers,
                    "nvr": rpm_info["nvr"],
                    "name": rpm_info["name"],
                    "version": rpm_info["version"],
                    "release": rpm_info["release"],
                    "epoch": rpm_info["epoch"] or 0,  # Default to epoch 0 if not specified (`None`)
                    "arch": rpm_info["arch"],
                    "source": ["koji.listRPMs", "koji.getRPMHeaders"],
                    "rpm_id": rpm_id,
                    # Our custom "source" key conflicts with the "SOURCE" RPM header
                    # So we call it "source_files" here instead
                    "source_files": headers["source"],
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
            bundled_provides = self._extract_bundled_provides(rpm_provides)
            if bundled_provides:
                bundled_components = self._parse_bundled_provides(bundled_provides, rpm_info)

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
    def _parse_bundled_provides(
        cls, bundled_provides: list[tuple[Component.Type, str, str]], rpm_info: dict[str, str]
    ) -> list[dict[str, Union[str, dict[str, Union[str, list[str]]]]]]:
        """Parse a list of (type, name, version) tuples, build a list of bundled component dicts"""
        id_counter = 0
        parsed_provides = []
        for component_type, bundled_component_name, version in bundled_provides:
            id_counter += 1
            bundled_component_meta: dict[str, Union[str, list[str]]] = {
                "name": bundled_component_name,
                "version": version,
                "rpm_id": f"{rpm_info['id']}-bundles-{id_counter}",
                "source": ["specfile"],
            }

            bundled_component: dict[str, Union[str, dict[str, Union[str, list[str]]]]] = {
                "type": component_type,
                "namespace": cls.check_red_hat_namespace(component_type, version),
                "meta": bundled_component_meta,
            }
            # We can't set go_component_type here for Golang components
            # Both Go modules and Go packages can be bundled into an RPM
            # There's no easy way for us to tell which type this component is
            parsed_provides.append(bundled_component)
        return parsed_provides

    @staticmethod
    def check_red_hat_namespace(
        component_type: str, version: str, publisher: str = ""
    ) -> Component.Namespace:
        """Given a component type, version, and optional publisher, return the correct namespace"""
        if publisher == "Red Hat, Inc.":
            # Components from Pyxis manifests may specify a publisher
            return Component.Namespace.REDHAT
        elif component_type in (
            Component.Type.RPM,
            Component.Type.RPMMOD,
            Component.Type.CONTAINER_IMAGE,
        ):
            # RPMs, modules, and containers are always built at Red Hat
            return Component.Namespace.REDHAT
        elif component_type == Component.Type.MAVEN and "redhat" in version:
            # .redhat or -redhat in the version string indicate this Maven component
            # was built in a Red Hat build system (e.g. by PNC / for a Middleware product)
            return Component.Namespace.REDHAT
        else:
            return Component.Namespace.UPSTREAM

    @staticmethod
    def _build_archive_dl_url(filename: str, build_info: dict[str, str]) -> str:
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
        name_label: str = "",
    ) -> dict[str, Any]:
        # A multi arch image is really just an OCI image index. From a container registry client
        # point of view they are transparent in that the client will always pull the correct arch
        # for their client without having the know the actual image location.
        # See https://github.com/opencontainers/image-spec/blob/main/image-index.md
        if any(item == "" for item in (name, version, release)):
            name, version, release = Brew.split_nvr(nvr)

        image_component: dict = {
            "type": Component.Type.CONTAINER_IMAGE,
            "brew_build_id": build_id,
            "meta": {
                "nvr": nvr,
                "name": name,
                "version": version,
                "release": release,
                "arch": arch,
            },
        }
        if name_label:
            image_component["meta"]["name_from_label_raw"] = name_label
            name_label_parts = name_label.rsplit("/", 1)
            if len(name_label_parts) == 2:
                image_component["meta"]["name_from_label"] = name_label_parts[1]
                image_component["meta"]["repository_url"] = f"{CONTAINER_REPOSITORY}/{name_label}"
                image_component["meta"]["name"] = name_label_parts[1]

        return image_component

    @staticmethod
    def split_nvr(nvr: str) -> tuple[str, str, str]:
        nvr_parts = nvr.rsplit("-", maxsplit=2)
        if len(nvr_parts) != 3:
            raise ValueError(f"NVR {nvr} had invalid length after splitting: {len(nvr_parts)}")
        name = nvr_parts[0]
        version = nvr_parts[1]
        release = nvr_parts[2]
        return name, version, release

    def get_container_build_data(self, build_id: int, build_info: dict[str, Any]) -> dict[str, Any]:
        component: dict[str, Any] = {
            "type": Component.Type.CONTAINER_IMAGE,
            "meta": {
                "name": build_info["name"],
                "version": build_info["version"],
                "release": build_info["release"],
                "epoch": build_info["epoch"] or 0,
                "arch": None,
                "source": ["koji.getBuild"],
            },
        }

        go_stdlib_version = ""
        remote_sources: dict[str, tuple[str, str]] = {}
        is_rhcos_build = build_info.get("cg_name") == self.RHCOS_BUILDER
        # TODO: Should we raise an error if build_info["extra"] is missing?
        if build_info["extra"]:
            image_info = build_info["extra"].get("image", {})
            index = image_info.get("index", {})
            if index:
                component["meta"]["digests"] = index["digests"]
                component["meta"]["pull"] = index.get("pull", [])

            if "parent_build_id" in image_info:
                parent_image = image_info["parent_build_id"]
                component["meta"]["parent"] = parent_image

            # These show up in multistage builds such as Build ID 1475846 and are build dependencies
            if "parent_image_builds" in image_info:
                build_parent_nvrs = []
                for parent_image_build in image_info["parent_image_builds"].values():
                    build_name, build_version, _ = Brew.split_nvr(parent_image_build["nvr"])
                    if "go-toolset" in build_name or "golang" in build_name:
                        build_parent_nvrs.append(build_name)
                        go_stdlib_version = build_version.removeprefix("v")
                        component["meta"]["go_stdlib_version"] = go_stdlib_version

                component["meta"]["build_parent_nvrs"] = build_parent_nvrs

            # Legacy OSBS builds such as 1890187 copy source code into dist-git but specify where
            # the source code came from using the 'go' stanza in container.yaml
            # ref: https://osbs.readthedocs.io/en/osbs_ocp3/users.html#go
            # Handle case when "go" key is present but value is None
            go = image_info.get("go", {})

            # AND handle case when "modules" key is present but value is None
            if go and go.get("modules", []):
                go_modules = tuple(
                    module["module"].removeprefix("https://")
                    for module in go["modules"]
                    if module.get("module")
                )
                if go_modules:
                    # Tuple above can be empty if .get("module") name is always None / an empty str
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

            # RHCOS builds do not have image archives for each arch build, instead each build
            # is already for a single arch which is listed in typeinfo
            if is_rhcos_build:
                component["meta"]["arch"] = build_info["extra"]["typeinfo"]["image"]["arch"]

        child_image_components: list[dict[str, Any]] = []
        archives = self.koji_session.listArchives(build_id)

        # Extract the list of embedded rpms
        noarch_rpms_by_id: dict[int, dict[str, Any]] = {}
        rpm_build_ids: set[int] = set()

        for archive in archives:
            # RHCOS builds do not have actual image, the tar actually contains the coreos-assembler
            # source code
            if archive["btype"] == "image" and archive["type_name"] == "tar" and not is_rhcos_build:
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
            if is_rhcos_build and archive["filename"] == "commitmeta.json":
                noarch_rpms_by_id = self._extract_rhcos_image_components(
                    archive, component, noarch_rpms_by_id, rpm_build_ids
                )

        source_components = self._extract_remote_sources(go_stdlib_version, remote_sources)

        component["nested_builds"] = list(rpm_build_ids)
        component["sources"] = source_components
        component["image_components"] = child_image_components
        component["components"] = list(noarch_rpms_by_id.values())

        # During Brew collection we are only able to inspect docker config labels on attached arch
        # specific archives. We do this loop here to save values from those labels onto the root
        # image container.
        for attr in (
            "name",
            "description",
            "license",
            "name_from_label",
            "repository_url",
            "name_from_label_raw",
        ):
            self._get_child_meta(component, attr)

        return component

    @staticmethod
    def _get_child_meta(component: dict[str, Any], meta_attr: str) -> None:
        for image in component["image_components"]:
            meta_attr_value = image["meta"].get(meta_attr)
            if meta_attr_value:
                component["meta"][meta_attr] = meta_attr_value
                break

    @classmethod
    def _extract_remote_sources(
        cls, go_stdlib_version: str, remote_sources: dict[str, tuple[str, str]]
    ) -> list[dict[str, Any]]:
        """Given a list of remote-source.json filenames / Cachito manifest names,
        build and return a list of source component dicts, one for each manifest
        Each source component has a "components" key with all the top-level
        .packages in that Cachito manifest, and
        Each top-level .package has a "components" key with all the child
        .dependencies of that package (e.g. .packages[index].dependencies in JQ)"""
        source_components: list[dict[str, Any]] = []
        for build_loc, coords in remote_sources.items():
            remote_source = cls._get_remote_source(coords[0])
            remote_source_name, remote_source_type = cls._parse_remote_source_url(
                remote_source.repo
            )
            source_component: dict[str, Any] = {
                "type": remote_source_type,
                "namespace": Component.Namespace.UPSTREAM,
                "meta": {
                    "name": remote_source_name,
                    "version": remote_source.ref,
                    "remote_source": coords[0],
                    "remote_source_archive": coords[1],
                    "source": [cls.KOJI_LISTARCHIVES_SRC],
                },
            }
            if build_loc:
                source_component["meta"]["cachito_build"] = build_loc
            logger.info(
                "Processing archive %s with package managers: %s",
                coords[0],
                remote_source.pkg_managers,
            )
            for pkg_type in remote_source.pkg_managers:
                # We process top-level .packages and all child .packages[].dependencies
                # This is enough to get all components from the manifest
                # The top-level .dependencies are duplicates
                # of each top-level .package's child .dependencies
                # We don't need to process them again
                if pkg_type in (
                    *cls.CARGO_TYPE_MAPPING,
                    *cls.GEM_TYPE_MAPPING,
                    *cls.NPM_TYPE_MAPPING,
                    *cls.PYPI_TYPE_MAPPING,
                ):
                    # Convert Cachito-reported package type to Corgi component type.
                    # TODO:  Add logging-kibana6-container-v6.8.1-362 to test data
                    #  use remote-source-kibana6.json manifest from Cachito
                    provides, remote_source.packages = cls._extract_provides(
                        remote_source.packages, pkg_type
                    )
                elif pkg_type in cls.GOLANG_TYPE_MAPPING:
                    provides, remote_source.packages = cls._extract_golang(
                        remote_source.packages, go_stdlib_version
                    )
                elif pkg_type == "git-submodule":
                    # Handle this type separately
                    # It's not necessarily guaranteed to be a GITHUB repo
                    # So we can't rely on a simple mapping like the other types
                    provides, remote_source.packages = cls._extract_submodules(
                        remote_source.packages, pkg_type
                    )
                else:
                    raise ValueError(f"Found unsupported remote-source pkg_manager {pkg_type}")

                try:
                    source_component["components"].extend(provides)
                except KeyError:
                    source_component["components"] = provides

            source_components.append(source_component)
        return source_components

    @classmethod
    def update_remote_sources(
        cls,
        archive: dict[str, str],
        build_info: dict[str, str],
        remote_sources: dict[str, tuple[str, str]],
    ) -> None:
        cachito_url = next(iter(remote_sources))
        logger.debug("Setting remote sources for %s using archive data %s", cachito_url, archive)
        remote_sources_url = cls._build_archive_dl_url(archive["filename"], build_info)
        # Update the remote sources download url tuple
        existing_coords = list(remote_sources[cachito_url])
        if archive["type_name"] == "tar":
            remote_sources[cachito_url] = (existing_coords[0], remote_sources_url)
        elif archive["type_name"] == "json":
            remote_sources[cachito_url] = (remote_sources_url, existing_coords[1])

    @staticmethod
    def extract_common_key(filename: str) -> str:
        without_prefix = filename.removeprefix("remote-source-")
        return without_prefix.split(".", 1)[0]

    def _extract_image_components(
        self,
        archive: dict[str, Any],
        build_id: int,
        build_nvr: str,
        noarch_rpms_by_id: dict[int, dict[str, Any]],
        rpm_build_ids: set[int],
    ) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
        logger.info("Processing image archive %s", archive["filename"])
        docker_config = archive["extra"]["docker"]["config"]
        labels = self._get_labels(docker_config)
        name_label = labels.get("name", "")
        child_component = self._create_image_component(
            build_id, build_nvr, arch=archive["extra"]["image"]["arch"], name_label=name_label
        )
        child_component["meta"]["description"] = labels.get("description", "")
        child_component["meta"]["docker_config"] = docker_config
        child_component["meta"]["filename"] = archive["filename"]
        child_component["meta"]["license"] = labels.get("License", "")
        child_component["meta"]["brew_archive_id"] = archive["id"]
        child_component["meta"]["digests"] = archive["extra"]["docker"]["digests"]
        child_component["meta"]["source"] = [self.KOJI_LISTARCHIVES_SRC]
        rpms = self.koji_session.listRPMs(imageID=archive["id"])
        arch_specific_rpms = []
        for rpm in rpms:
            rpm_component = self._create_rpm_component_definition(rpm)
            rpm_build_ids.add(rpm["build_id"])
            if rpm["arch"] == "noarch":
                noarch_rpms_by_id[rpm["id"]] = rpm_component
            else:
                arch_specific_rpms.append(rpm_component)
        child_component["rpm_components"] = arch_specific_rpms
        return noarch_rpms_by_id, child_component

    def _extract_rhcos_image_components(
        self,
        archive: dict[str, Any],
        component: dict[str, Any],
        noarch_rpms_by_id: dict[int, dict[str, Any]],
        rpm_build_ids: set[int],
    ) -> dict[int, dict[str, Any]]:
        logger.info("Processing RHCOS image archive %s", archive["filename"])
        component["meta"]["filename"] = archive["filename"]
        component["meta"]["brew_archive_id"] = archive["id"]
        component["meta"]["source"] = [self.KOJI_LISTARCHIVES_SRC]
        rpms = self.koji_session.listRPMs(imageID=archive["id"])
        for rpm in rpms:
            rpm_component = self._create_rpm_component_definition(rpm)
            rpm_build_ids.add(rpm["build_id"])
            noarch_rpms_by_id[rpm["id"]] = rpm_component
        return noarch_rpms_by_id

    @staticmethod
    def _create_rpm_component_definition(rpm: dict[str, Any]):
        """Creates the definition of an RPM build from the data given by koji's listRPMs."""
        return {
            "type": Component.Type.RPM,
            "namespace": Component.Namespace.REDHAT,
            "brew_build_id": rpm["build_id"],
            "meta": {
                "nvr": rpm["nvr"],
                "name": rpm["name"],
                "version": rpm["version"],
                "release": rpm["release"],
                "arch": rpm["arch"],
                "rpm_id": rpm["id"],
                "source": ["koji.listRPMs"],
            },
        }

    @staticmethod
    def _get_labels(docker_config: dict[str, dict[str, dict[str, str]]]) -> dict[str, str]:
        config = docker_config.get("config", {})
        return config.get("Labels", {})

    @classmethod
    def _extract_provides(
        cls, packages: list[SimpleNamespace], pkg_type: str
    ) -> tuple[list[dict[str, Any]], list[SimpleNamespace]]:
        components: list[dict[str, Any]] = []
        typed_pkgs, remaining_packages = cls._filter_by_type(packages, pkg_type)
        for typed_pkg in typed_pkgs:
            component_type = cls.CACHITO_PKG_TYPE_MAPPING[pkg_type]
            typed_component: dict[str, Any] = {
                "type": component_type,
                "namespace": cls.check_red_hat_namespace(component_type, typed_pkg.version),
                "meta": {
                    "name": typed_pkg.name,
                    "version": typed_pkg.version,
                },
            }
            # Sometimes a top-level package has a "path" key
            # e.g. for npm or go-package components nested into a subfolder
            try:
                typed_component["meta"]["path"] = typed_pkg.path
            except AttributeError:
                pass

            typed_component["components"] = []
            for dep in typed_pkg.dependencies:
                component_meta = {
                    "name": dep.name,
                    "version": dep.version,
                }
                component_type = cls.CACHITO_PKG_TYPE_MAPPING[dep.type]
                component = {
                    "type": component_type,
                    "namespace": cls.check_red_hat_namespace(component_type, dep.version),
                    "meta": component_meta,
                }
                # The dev key is only present for Cachito package managers which support
                # dev dependencies. See https://github.com/containerbuildsystem/cachito/blob/
                # f3e954e3d04d2cd35cc878c1189cd55e7471220d/docs/metadata.md#dependencydev
                if hasattr(dep, "dev"):
                    component_meta["dev"] = dep.dev
                typed_component["components"].append(component)
            components.append(typed_component)
        return components, remaining_packages

    @classmethod
    def _extract_golang(
        cls, dependencies: list[SimpleNamespace], go_stdlib_version: str = ""
    ) -> tuple[list[dict[str, Any]], list[SimpleNamespace]]:
        """Given a list of Golang module and package objects in some Cachito manifest,
        build and return a list of Golang module and package dicts"""
        # We no longer move go-packages like golang.org/x/text/cases
        # underneath modules with matching names like golang.org/x/text
        # because this complicates the code / caused several bugs,
        # and we should be relying on Cachito's dependency tree anyway
        modules, remaining_deps = cls._filter_by_type(dependencies, "gomod")
        packages, remaining_deps = cls._filter_by_type(remaining_deps, "go-package")

        # Build a list of package dicts from package objects
        package_dicts = cls._build_golang_component_dict_from_objs(go_stdlib_version, packages)
        # Build a list of module dicts from module objects
        module_dicts = cls._build_golang_component_dict_from_objs(go_stdlib_version, modules)

        return [*module_dicts, *package_dicts], remaining_deps

    @classmethod
    def _build_golang_component_dict_from_objs(
        cls, go_stdlib_version: str, dependent_obj_list: list[SimpleNamespace]
    ) -> list[dict[str, Any]]:
        """Given a list of dependent Golang component objects,
        build and return a list of dependent Golang component dicts"""
        dependent_dict_list: list[dict[str, Any]] = []
        for dep in dependent_obj_list:
            # We set properties for this component assuming it's a gomod or go-package
            # And filter out top-level .packages with different types
            # But since this method is recursive and processes child .dependencies,
            # raise an error if we see a non-Golang type
            # That means some top-level gomod or go-package in .packages
            # depends on a non-Golang child in .packages[index].dependencies
            # Probably shouldn't happen but we can't guarantee this
            dependent_dict: dict[str, Any] = {
                # Should be GOLANG, or raise a KeyError if not
                "type": cls.GOLANG_TYPE_MAPPING[dep.type],
                "namespace": Component.Namespace.UPSTREAM,
                "meta": {
                    # Could be gomod or go-package
                    "go_component_type": dep.type,
                    "name": dep.name.removeprefix("vendor/"),
                    # stdlib components get their versions from the golang compiler
                    "version": dep.version or go_stdlib_version,
                },
            }
            # Report dev if it's present, whether it's True or False
            # Only present for child .dependencies as far as I can tell
            if hasattr(dep, "dev"):
                dependent_dict["meta"]["dev"] = dep.dev
            # Report path if it's not empty / None
            # Only present for top-level .packages as far as I can tell
            path = getattr(dep, "path", "")
            if path:
                dependent_dict["meta"]["path"] = path

            nested_deps: list[SimpleNamespace] = getattr(dep, "dependencies", [])
            if nested_deps:
                dependent_dict["components"] = cls._build_golang_component_dict_from_objs(
                    go_stdlib_version, nested_deps
                )
            dependent_dict_list.append(dependent_dict)

        return dependent_dict_list

    @classmethod
    def _extract_submodules(
        cls, packages: list[SimpleNamespace], pkg_type: str
    ) -> tuple[list[dict[str, Any]], list[SimpleNamespace]]:
        """Given a list of git submodules in some Cachito manifest,
        build and return a list of GITHUB component dicts
        Raise a ValueError if some component isn't hosted on Github"""
        # All the examples of this type I saw were for Github repos
        # Fail to process anything that's not, just in case
        # In the future, we can add support for other Git services if needed
        # e.g. Bitbucket, Gitlab, etc.
        # Raising an error makes these edge cases visible so we know to do this
        # We won't silently ignore / skip certain components
        # so we won't need to reprocess all builds with this type later

        components: list[dict[str, Any]] = []
        typed_pkgs, remaining_packages = cls._filter_by_type(packages, pkg_type)
        for typed_pkg in typed_pkgs:
            if not typed_pkg.version.startswith("https://github.com/"):
                # If we ever see http:// or https://www. or git@github.com:user/repo forms,
                # let's add logic to handle them later instead of being too permissive
                raise ValueError(
                    f"git-submodule package is not hosted on Github: {typed_pkg.version}"
                )

            # Values are like https://github.com/user_namespace/repo_name#commit_hash
            name_and_version = typed_pkg.version.rsplit("#", maxsplit=1)
            if len(name_and_version) == 1:
                raise ValueError(
                    f"Couldn't identify version / commit ID for package: {typed_pkg.version}"
                )

            name, version = name_and_version
            name = name.replace("https://github.com/", "", 1)
            if name.endswith(".git"):
                name = name.replace(".git", "", 1)

            typed_component: dict[str, Any] = {
                "type": Component.Type.GITHUB,
                "namespace": Component.Namespace.UPSTREAM,
                "meta": {
                    # The name of the submodule in the parent repo
                    "module_name": typed_pkg.name,
                    # Should be the name of the child repo we included by this point
                    "name": name,
                    # Path to the submodule in the parent repo
                    # Usually the same as the submodule name, but not always
                    # e.g. typed_pkg.name == "grpc", typed_pkg.path == "third_party/grpc"
                    # This path shouldn't be used as a qualifier in this component's purl
                    # It represents the path in the parent repo / component
                    # Not the repo / component we're creating here
                    # TODO: Check the source / root component we create
                    #  We have Git repo names and commit IDs in Cachito's JSON data
                    #  in .repo and .ref top-level keys, respectively
                    #  Are we using them anywhere? We should store in meta_attr at least
                    "path": typed_pkg.path,
                    # Should be a commit ID / hash by this point
                    "version": version,
                },
            }
            if hasattr(typed_pkg, "dev"):
                typed_component["meta"]["dev"] = typed_pkg.dev

            # This code should be unused
            # All git-submodule examples I looked at had no child .dependencies
            dependencies = getattr(typed_pkg, "dependencies", [])
            if dependencies:
                typed_component["components"], remaining_dependencies = cls._extract_submodules(
                    dependencies, pkg_type
                )
                if remaining_dependencies:
                    # We filtered out child .dependencies of this top-level .package
                    # because they didn't have git-submodule type
                    # Since they're not top-level .packages themselves,
                    # they won't get processed anywhere else in our code
                    # So in theory we'd be missing components
                    # This shouldn't happen, but raise an error just in case
                    raise ValueError(
                        f"Top-level package {typed_pkg.version} had {len(remaining_dependencies)} "
                        "child dependencies that were not Git submodules"
                    )
            components.append(typed_component)
        return components, remaining_packages

    @staticmethod
    def _filter_by_type(
        dependencies: list[SimpleNamespace], pkg_type: str
    ) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
        filtered: list[SimpleNamespace] = []
        remaining_deps = dependencies[:]
        for dep in dependencies:
            if dep.type == pkg_type:
                filtered.append(dep)
                remaining_deps.remove(dep)
        return filtered, remaining_deps

    @staticmethod
    def extract_advisory_ids(build_tags: list[str]) -> list[str]:
        """From a Brew build's list of tags, return any errata IDs with -released stripped"""
        advisory_ids = set()
        for tag in build_tags:
            match = ADVISORY_REGEX.match(tag)
            if match:
                advisory_ids.add(match.group())
        return sorted(advisory_ids)

    @staticmethod
    def parse_advisory_ids(errata_tags: list[str]) -> list[str]:
        """From a Brew build's list of Errata tags, return tags with released (4-digit) IDs"""
        # released errata always have 4-digit IDs, e.g. RHBA-2023:1234
        # unreleased errata have 5-digit IDs or greater
        # e.g. RHEA-2023:12345 or RHSA-2023:123456
        # tags in Brew also have a -released, -dropped, or -pending suffix
        # but our ADVISORY_REGEX strips this to get just the friendly advisory name

        return sorted(
            errata_tag
            for errata_tag in errata_tags
            if len(errata_tag.split(":", maxsplit=1)[-1]) == 4
        )

    @staticmethod
    def get_module_build_data(build_info: dict[str, Any]) -> dict[str, Any]:
        modulemd_yaml = build_info["extra"]["typeinfo"]["module"].get("modulemd_str", "")
        if not modulemd_yaml:
            raise ValueError("Cannot get module build data, modulemd_yaml is undefined")
        modulemd = yaml.safe_load(modulemd_yaml)
        meta_attr = {
            "stream": modulemd["data"]["stream"],
            "context": modulemd["data"]["context"],
            "components": modulemd["data"].get("components", []),
            "rpms": modulemd["data"]["xmd"]["mbs"].get("rpms", []),
            "source": ["koji.getBuild"],
        }
        module = {
            "type": Component.Type.RPMMOD,
            "namespace": Component.Namespace.REDHAT,
            "meta": {
                "name": build_info["name"],
                "version": build_info["version"],
                # TODO: Need to verify this
                "license_declared_raw": " OR ".join(modulemd["data"]["license"].get("module", "")),
                "release": build_info["release"],
                "description": modulemd["data"]["description"],
                "meta_attr": meta_attr,
            },
        }

        return module

    # Force clients to call this using an int build_id
    def get_component_data(self, build_id: int) -> dict[str, Any]:
        logger.info("Retrieving Brew build: %s", build_id)
        # koji api expects a build_id to be an int. If you pass a string it'll look for an NVR
        build = self.koji_session.getBuild(build_id)
        if not build:
            raise BrewBuildNotFound(f"Build {build_id} was not found")
        # getBuild will accept an NVR
        # but later brew calls require an integer ID
        build_id = build["id"]
        # Determine build state
        state = build.get("state")
        if state == koji.BUILD_STATES["DELETED"]:
            app.send_task(
                "corgi.tasks.brew.slow_delete_brew_build",
                args=(build_id, state),
            )
            return {}
        elif state != koji.BUILD_STATES["COMPLETE"]:
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
            # or other types of images.
            build_extra = build.get("extra")
            if build["cg_name"] in {"atomic-reactor", self.RHCOS_BUILDER}:
                # Check the content generator name to determine where this
                # image was built, which indicates what type of image it is.
                # Container images are built in OSBS, which uses atomic-reactor to build them.
                # RHCOS images are built with coreos-assembler
                pass
            elif build_extra and build_extra.get("submitter") == "osbs":
                # Some builds such as 903565 have the cg_name field set to None
                # In that case check the extra/submitter field for osbs value
                pass
            else:
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

        # Clean source URL on old builds which are being reloaded, if needed
        build["source"] = self.clean_source_of_build(build["source"])

        # Add list of Brew tags for this build
        tags = self.koji_session.listTags(build_id)
        build["tags"] = sorted(set(tag["name"] for tag in tags))
        build["errata_tags"] = self.extract_advisory_ids(build["tags"])
        build["released_errata_tags"] = self.parse_advisory_ids(build["errata_tags"])

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
    ) -> tuple[str, ...]:
        try:
            builds = self.koji_session.listTagged(brew_tag, inherit=inherit, latest=latest)
            return tuple(b["build_id"] for b in builds)
        except koji.GenericError as exc:  # type: ignore[attr-defined]
            logger.warning("Couldn't find brew builds with tag %s: %s", brew_tag, exc)
            return tuple()

    def brew_rpm_headers_lookup(
        self, rpm_infos: list[dict[str, str]]
    ) -> tuple[tuple[dict[str, str], Any], ...]:
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
        with self.koji_session.multicall() as m:
            rpm_info_header_calls = tuple(
                (rpm_info, m.getRPMHeaders(rpmID=rpm_info["id"], headers=rpm_headers))
                for rpm_info in rpm_infos
            )
        return rpm_info_header_calls

    def brew_srpm_lookup(self, srpms: Iterable[str]) -> tuple[tuple[str, Any], ...]:
        """The Koji API findBuild call can except NVR as a format"""
        with self.koji_session.multicall() as multicall:
            find_build_id_calls = tuple((srpm, multicall.findBuildID(srpm)) for srpm in srpms)
        return find_build_id_calls

    def brew_rpm_lookup(self, rpms: tuple[str, ...]) -> tuple[tuple[str, Any], ...]:
        """The Koji API getRPM call can except rpm in NVR"""
        with self.koji_session.multicall() as multicall:
            get_rpm_calls = tuple((rpm, multicall.getRPM(rpm)) for rpm in rpms)
        return get_rpm_calls

    @classmethod
    def sans_epoch(cls, rpm: str) -> str:
        """This removed the epoch part of a SRPM or RPM so the RPM name is in NVR format"""
        name, version, release = cls.split_nvr(rpm)
        version_parts = version.split(":")
        if len(version_parts) > 1:
            rpm = f"{name}-{version_parts[1]}-{release}"
        return rpm

    @staticmethod
    def module_key_to_nvr(module_key: str) -> str:
        """This adjusts the rhel_module name found in composes to be in NVR format expected by
        the Koji API"""
        module_parts = module_key.split(":")
        return f"{module_parts[0]}-{module_parts[1]}-{module_parts[2]}.{module_parts[3]}"

    def persist_modules(self, rhel_modules: dict[str, list[str]]) -> Generator[str, None, None]:
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
            rpms = tuple(
                self.sans_epoch(rpm) for rpm in rhel_modules[srpm] if not rpm.endswith(".src")
            )
            rpm_lookup_calls = self.brew_rpm_lookup(rpms)
            for rpm, call in rpm_lookup_calls:
                srpm_build_id = call.result["build_id"]
                srpm_obj, _ = CollectorSRPM.objects.get_or_create(build_id=srpm_build_id)
                rpm_obj, _ = CollectorRPM.objects.get_or_create(nvra=rpm, srpm=srpm_obj)
                rpm_obj.rhel_module.add(rhel_module)

            yield build_id

    def lookup_build_ids(
        self, rpm_filenames_by_srpm: dict[str, list[str]]
    ) -> Generator[str, None, None]:
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

    @staticmethod
    def fetch_rhel_module(build_id: str) -> dict[str, Any]:
        """Look up a RHEL module by either an integer build_id or an NVR."""
        try:
            lookup: dict = {"build_id": int(build_id)}
        except ValueError:
            lookup = {"nvr": build_id}
        try:
            rhel_module = CollectorRhelModule.objects.get(**lookup)
        except CollectorRhelModule.DoesNotExist:
            logger.debug("Did not find %s in CollectorRhelModule data", build_id)
            return {}
        name, version, release = Brew.split_nvr(rhel_module.nvr)
        module: dict[str, Any] = {
            "type": Component.Type.RPMMOD,
            "namespace": Component.Namespace.REDHAT,
            "meta": {
                "name": name,
                "version": version,
                "release": release,
                "source": ["collectors/rhel_module"],
            },
        }
        nested_builds: set[int] = set()
        rpm_components: list[dict] = []
        for rpm in rhel_module.collectorrpm_set.get_queryset():
            srpm_build_id = rpm.srpm.build_id
            nested_builds.add(srpm_build_id)
            name, version, release = Brew.split_nvr(rpm.nvra)
            release_split = release.rsplit(".", 1)
            arch = ""
            if len(release_split) == 2:
                arch = release_split[1]
            rpm_component: dict = {
                "type": Component.Type.RPM,
                "namespace": Component.Namespace.REDHAT,
                "brew_build_id": srpm_build_id,
                "meta": {
                    "name": name,
                    "version": version,
                    "release": release_split[0],
                    "arch": arch,
                    "source": ["collectors/rhel_module"],
                },
            }
            rpm_components.append(rpm_component)
        module["components"] = rpm_components
        module["nested_builds"] = list(nested_builds)
        return module

import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Generator, Tuple

import requests

from corgi.collectors.brew import Brew
from corgi.collectors.models import (
    CollectorComposeRhelModule,
    CollectorComposeRPM,
    CollectorComposeSRPM,
)

logger = logging.getLogger(__name__)


class RhelCompose:
    @classmethod
    def fetch_rhel_module(cls, build_id: int) -> dict[str, Any]:
        try:
            rhel_module = CollectorComposeRhelModule.objects.get(build_id=build_id)
        except CollectorComposeRhelModule.DoesNotExist:
            logger.debug("Did not find %s in CollectorComposeRhelModule data", build_id)
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
                "source": ["collectors/rhel_compose"],
            },
        }
        nested_builds: set[int] = set()
        rpm_components: list[dict] = []
        for rpm in rhel_module.collectorcomposerpm_set.all():
            srpm_build_id = rpm.srpm.build_id
            nested_builds.add(srpm_build_id)
            name, version, release = Brew.split_nvr(rpm.nvra)
            release_split = release.rsplit(".", 1)
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
                "analysis_meta": {"source": "collectors/rhel_compose"},
            }
            rpm_components.append(rpm_component)
        module["components"] = rpm_components
        module["nested_builds"] = list(nested_builds)
        return module

    @classmethod
    def fetch_compose_data(
        cls, compose_url: str, variants: list[str]
    ) -> Tuple[str, datetime, dict]:
        compose_data: dict = {
            "srpms": [],
            "container_images": [],
            "rhel_modules": [],
        }
        compose_url = compose_url.rstrip("/") + "/metadata/"
        logger.info("Fetching compose data from %s", compose_url)

        # Fetch general compose info file to extract the date timestamp the compose was created
        # and the list of variants in this compose. All variants have empty lists created that
        # will be filled in below.
        response = requests.get(compose_url + "composeinfo.json")
        response.raise_for_status()
        compose_info = response.json()
        compose_id = compose_info["payload"]["compose"]["id"]
        compose_created_date = compose_info["payload"]["compose"]["date"]
        compose_created_date = datetime.strptime(compose_created_date, "%Y%m%d")

        compose_data["srpms"] = list(cls._fetch_rpm_data(compose_url, variants))

        # Fetch a list of container images associated with this compose; the "nvr" attribute was
        # added in later versions of this metadata file, so we construct it ourselves by
        # concatenating n, v, and r.
        response = requests.get(compose_url + "osbs.json")
        if response.ok:
            for variant, variant_images in response.json().items():
                if variant in variants:
                    container_images: set = set()
                    for arch, images in variant_images.items():
                        container_images.update(
                            f"{image['name']}-{image['version']}-{image['release']}"
                            for image in images
                        )
                    compose_data["container_images"] = list(container_images)

        compose_data["rhel_modules"] = list(cls._fetch_module_data(compose_url, variants))
        return compose_id, compose_created_date, compose_data

    @classmethod
    def _fetch_module_data(cls, compose_url, variants):
        # Fetch a list of RHEL modules.
        response = requests.get(compose_url + "modules.json")
        brew = Brew()
        rhel_modules = {}
        if response.ok:
            for variant, variant_modules in response.json()["payload"]["modules"].items():
                if variant in variants:
                    for arch, modules in variant_modules.items():
                        for module_key in modules.keys():
                            rpms = [
                                cls.sans_epoch(rpm, brew) for rpm in modules[module_key]["rpms"]
                            ]
                            rhel_modules[cls.module_key_to_nvr(module_key)] = {"rpms": rpms}
        # For each rhel_module look up it's build_id
        find_build_id_calls = brew.brew_srpm_lookup(rhel_modules.keys())
        for srpm, call in find_build_id_calls:
            build_id = call.result
            rhel_module, _ = CollectorComposeRhelModule.objects.get_or_create(
                build_id=build_id,
                nvr=srpm,
            )
            # Lookup the rpm build_ids
            rpm_lookup_calls = brew.brew_rpm_lookup(rhel_modules[srpm]["rpms"])
            for rpm, call in rpm_lookup_calls:
                srpm_build_id = call.result["build_id"]
                srpm, _ = CollectorComposeSRPM.objects.get_or_create(build_id=srpm_build_id)
                rpm_obj, _ = CollectorComposeRPM.objects.get_or_create(
                    nvra=rpm,
                    srpm=srpm,
                )
                rpm_obj.rhel_module.add(rhel_module)

            yield build_id

    @classmethod
    def _fetch_rpm_data(cls, compose_url: str, variants: list[str]) -> Generator[str, None, None]:
        # Fetch list of SRPMs. These include epoch! We don't bother indexing this by arch since
        # we can look up the SRPM from our component data and get the information from there.
        brew = Brew()
        response = requests.get(compose_url + "rpms.json")
        if response.ok:
            rpm_filenames_by_srpm = defaultdict(list)
            for variant, variant_rpms in response.json()["payload"]["rpms"].items():
                if variant in variants:
                    for arch, rpms in variant_rpms.items():
                        for rpm, rpm_details in rpms.items():
                            for rpm_detail in rpm_details.values():
                                rpm_filenames_by_srpm[
                                    cls.sans_epoch(rpm.removesuffix(".src"), brew)
                                ].append(os.path.basename(rpm_detail["path"]))
            # There are 2 loops here to utilize Brew multicall
            srpms = rpm_filenames_by_srpm.keys()
            find_build_id_calls = brew.brew_srpm_lookup(srpms)
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
                        rpm_data = brew.koji_session.getRPM(filename)
                        if not rpm_data:
                            # Try the next srpm rpm filename
                            continue
                        build_id = rpm_data["build_id"]
                        # found the build_id, stop iterating filenames
                        break
                    # if no filenames had RPM data
                    if not build_id:
                        logger.warning(
                            "Unable to find build_id for %s when saving %s", srpm, build_id
                        )
                yield build_id

    @classmethod
    def module_key_to_nvr(cls, module_key):
        module_parts = module_key.split(":")
        return f"{module_parts[0]}-{module_parts[1]}-{module_parts[2]}.{module_parts[3]}"

    @classmethod
    def sans_epoch(cls, srpm, brew):
        name, version, release = brew.split_nvr(srpm)
        version_parts = version.split(":")
        if len(version_parts) > 1:
            srpm = f"{name}-{version_parts[1]}-{release}"
        return srpm

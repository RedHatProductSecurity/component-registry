import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Generator, Tuple

import requests

from corgi.collectors.brew import Brew

logger = logging.getLogger(__name__)


class RhelCompose:
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
                            rhel_modules[brew.module_key_to_nvr(module_key)] = modules[module_key][
                                "rpms"
                            ]
        yield from brew.persist_modules(rhel_modules)

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
                                rpm_filenames_by_srpm[brew.sans_epoch(rpm)].append(
                                    os.path.basename(rpm_detail["path"])
                                )

            yield from brew.lookup_build_ids(rpm_filenames_by_srpm)

import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import Tuple

import requests
from django.conf import settings

from corgi.collectors.brew import Brew

logger = logging.getLogger(__name__)


class RhelCompose:

    SUPPORTED_RHEL_MAJOR_VERSIONS = (7, 8)

    @classmethod
    def fetch_compose_data(cls, compose_data: tuple) -> dict:
        compose_id, compose_url = compose_data
        logger.info("Fetching compose data for %s from %s", compose_id, compose_url)
        variant_to_data: dict = {}

        # Fetch general compose info file to extract the date timestamp the compose was created
        # and the list of variants in this compose. All variants have empty lists created that
        # will be filled in below.
        compose_info = requests.get(compose_url + "composeinfo.json").json()
        compose_created_date = compose_info["payload"]["compose"]["date"]
        compose_created_date = datetime.strptime(compose_created_date, "%Y%m%d")

        for variant in compose_info["payload"]["variants"]:
            variant_to_data[variant] = {
                "srpms": {},
                "container_images": [],
                "rhel_modules": [],
            }

        # Fetch list of SRPMs. These include epoch! We don't bother indexing this by arch since
        # we can look up the SRPM from our component data and get the information from there.
        response = requests.get(compose_url + "rpms.json")
        if response.ok:
            for variant, variant_rpms in response.json()["payload"]["rpms"].items():
                rpm_filnames_by_srpm = defaultdict(list)
                for arch, rpms in variant_rpms.items():
                    for rpm, rpm_details in rpms.items():
                        for rpm_detail in rpm_details.values():
                            rpm_filnames_by_srpm[cls.sans_epoch(rpm.removesuffix(".src"))].append(
                                os.path.basename(rpm_detail["path"])
                            )
                variant_to_data[variant]["srpms"] = rpm_filnames_by_srpm

        # Fetch a list of container images associated with this compose; the "nvr" attribute was
        # added in later versions of this metadata file, so we construct it ourselves by
        # concatenating n, v, and r.
        response = requests.get(compose_url + "osbs.json")
        if response.ok:
            for variant, variant_images in response.json().items():
                container_images: set = set()
                for arch, images in variant_images.items():
                    container_images.update(
                        f"{image['name']}-{image['version']}-{image['release']}" for image in images
                    )
                variant_to_data[variant]["container_images"] = list(container_images)

        # Fetch a list of RHEL modules. We don't store their lists of RPMs since we can look that
        # up in our component data for the associated RHEL module build.
        response = requests.get(compose_url + "modules.json")
        if response.ok:
            for variant, variant_modules in response.json()["payload"]["modules"].items():
                rhel_modules = set()
                for arch, modules in variant_modules.items():
                    rhel_modules.update(modules.keys())
                variant_to_data[variant]["rhel_modules"] = list(rhel_modules)

        return {compose_id: {"ts": compose_created_date, "data": variant_to_data}}

    @classmethod
    def fetch_compose_versions(cls) -> dict[str, list[Tuple[str, str]]]:
        versions_by_minor = defaultdict(list)
        for rhel_major_version in cls.SUPPORTED_RHEL_MAJOR_VERSIONS:
            compose_list_url = (
                f"{settings.RHEL_COMPOSE_BASE_URL}"
                f"/rhel-{rhel_major_version}/rel-eng/RHEL-{rhel_major_version}/"
            )
            response = requests.get(compose_list_url)
            # TODO: file RFE for RCM to add a top-level compose_list.json file with this data so we
            #  don't have to parse HTML with regexes...https://stackoverflow.com/a/1732454/864413
            for line in response.text.split("\n"):
                # https://regex101.com/r/wnyFda/1
                match = re.search(r">(RHEL-(\d+\.\d+)[^/]+)/</a>\s+(\d.*)", line)
                if match:
                    compose_id = match.groups()[0]
                    compose_version_url = f"{compose_list_url}{compose_id}/compose/metadata/"
                    versions_by_minor[match.groups()[1]].append((compose_id, compose_version_url))
        return versions_by_minor

    @classmethod
    def sans_epoch(cls, srpm):
        name, version, release = Brew.split_nvr(srpm)
        version_parts = version.split(":")
        if len(version_parts) > 1:
            srpm = f"{name}-{version_parts[1]}-{release}"
        return srpm

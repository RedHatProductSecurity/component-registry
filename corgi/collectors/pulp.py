import copy
import logging
from collections import defaultdict
from typing import Generator

import requests
from django.conf import settings

from corgi.collectors.brew import Brew
from corgi.collectors.models import CollectorRPMRepository

logger = logging.getLogger(__name__)

PAGE_SIZE = 500

RPM_CRITERIA = {
    "criteria": {
        "fields": {"unit": ["filename", "sourcerpm"]},
        "sort": {"unit": None},
        "type_ids": ["rpm"],
        "limit": PAGE_SIZE,
        "skip": 0,
    }
}

MODULE_CRITERIA = {
    "criteria": {
        "fields": {"unit": ["name", "stream", "artifacts", "version", "context", "arch"]},
        "type_ids": ["modulemd"],
    }
}


class Pulp:
    def __init__(self):
        self.session = requests.Session()

    def get_active_repositories(self) -> int:
        """Fetch all Pulp repos which had something shipped in them, and their content sets."""
        criteria = {
            "criteria": {
                "fields": ["id", "notes"],
                "filters": {"last_unit_added": {"$ne": None}},
            }
        }
        url = f"{settings.PULP_URL}/api/v2/repositories/search/"
        auth = (settings.PULP_USERNAME, settings.PULP_PASSWORD)
        response = self.session.post(url, json=criteria, auth=auth)
        response.raise_for_status()
        no_created = 0
        for repo in response.json():
            content_set = repo["notes"].get("content_set", "")
            _, created = CollectorRPMRepository.objects.update_or_create(
                name=repo["id"], defaults={"content_set": content_set}
            )
            if created:
                no_created += 1
                logger.info("Created CollectorRPMRepository with id %s", repo["id"])
        return no_created

    def get_module_data(self, repo: str) -> Generator[str, None, None]:
        module_data = self._get_module_data(repo)
        yield from Brew().persist_modules(module_data)

    def _get_module_data(self, repo) -> dict[str, list[str]]:
        module_data = self._get_unit_data(repo, MODULE_CRITERIA)
        rpms_by_module = {}
        for entry in module_data:
            name = entry["metadata"]["name"]
            # eg. build_id 1500134
            stream = entry["metadata"]["stream"].replace("-", "_")
            version = entry["metadata"]["version"]
            context = entry["metadata"]["context"]
            module_key = f"{name}-{stream}-{version}.{context}"
            rpms_by_module[module_key] = entry["metadata"]["artifacts"]
        return rpms_by_module

    def get_rpm_data(self, repo: str) -> Generator[str, None, None]:
        rpms_by_srpm = self._get_rpm_data(repo)
        yield from Brew().lookup_build_ids(rpms_by_srpm)

    def _get_rpm_data(self, repo) -> defaultdict:
        rpms_by_srpm = defaultdict(list)
        page = 0
        rpm_criteria = copy.deepcopy(RPM_CRITERIA)
        while True:
            rpm_criteria["criteria"]["skip"] = PAGE_SIZE * page
            page += 1
            rpm_data = self._get_unit_data(repo, rpm_criteria)
            for entry in rpm_data:
                filename = entry["metadata"]["filename"]
                if filename.endswith(".src"):
                    continue
                source_rpm = entry["metadata"]["sourcerpm"].removesuffix(".src.rpm")
                rpms_by_srpm[source_rpm].append(filename)
            if len(rpm_data) < PAGE_SIZE:
                break
        return rpms_by_srpm

    def _get_unit_data(self, repo: str, criteria: dict) -> dict:
        url = f"{settings.PULP_URL}/api/v2/repositories/{repo}/search/units/"
        auth = (settings.PULP_USERNAME, settings.PULP_PASSWORD)
        response = self.session.post(url, json=criteria, auth=auth)
        if response.status_code == 404:
            logger.warning("No pulp units found for repo: %s", repo)
            return {}
        response.raise_for_status()
        return response.json()

import requests

from corgi.collectors.models import CollectorSpdxLicense

SPDX_LICENSE_LIST_URL = (
    "https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json"
)


class Spdx:
    @classmethod
    def get_spdx_license_list(cls) -> str:
        response = requests.get(SPDX_LICENSE_LIST_URL)
        response.raise_for_status()
        data = response.json()
        version = data["licenseListVersion"]
        for entry in data["licenses"]:
            identifier = entry.pop("licenseId")
            name = entry.pop("name", "")
            reference = entry.pop("reference", "")
            details_url = entry.pop("detailsUrl", "")
            CollectorSpdxLicense.objects.update_or_create(
                identifier=identifier,
                defaults={
                    "name": name,
                    "reference": reference,
                    "details_url": details_url,
                    "meta_attr": entry,
                },
            )
        return version

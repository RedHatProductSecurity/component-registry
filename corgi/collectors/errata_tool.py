import logging
import typing
from collections import defaultdict
from functools import reduce

import requests
from django.conf import settings
from requests import HTTPError
from requests_gssapi import HTTPSPNEGOAuth

from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)

logger = logging.getLogger(__name__)


class ErrataTool:
    """Interface to the Errata Tool APIs."""

    GSSAPI_AUTH = HTTPSPNEGOAuth()

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = self.GSSAPI_AUTH

    def get(
        self,
        path: str,
        **request_kwargs: typing.Any,
    ) -> dict:
        """Get the response to a REST API call or raise an exception."""
        url = f"{settings.ERRATA_TOOL_URL}/{path}"
        response = self.session.get(url, **request_kwargs)
        response.raise_for_status()
        return response.json()

    def get_paged(
        self, path: str, page_data_attr: typing.Optional[str] = None, pager: str = "page[number]"
    ):
        """Generator to iterate over data from paged Errata Tool API endpoint."""
        params = {pager: 1}

        while True:
            page = self.get(path, params=params)
            if page_data_attr:
                page = page[page_data_attr]
            if page:
                yield from page
                params[pager] += 1
            else:
                break

    def load_et_products(self):
        products = self.get_paged("api/v1/products", page_data_attr="data")
        for product in products:
            et_product, created = CollectorErrataProduct.objects.get_or_create(
                et_id=product["id"],
                name=product["attributes"]["name"],
                short_name=product["attributes"]["short_name"],
            )
            if created:
                logger.info("Created ET Product: %s", et_product.short_name)
            for product_version in product["relationships"]["product_versions"]:
                et_product_version, created = CollectorErrataProductVersion.objects.get_or_create(
                    et_id=product_version["id"], name=product_version["name"], product=et_product
                )
                if created:
                    logger.info("Created ET ProductVersion: %s", et_product_version.name)
        for pv in CollectorErrataProductVersion.objects.all():
            pv_details = self.get(f"api/v1/products/{pv.product.et_id}/product_versions/{pv.et_id}")
            brew_tags = [t.removesuffix("-candidate") for t in pv_details["data"]["brew_tags"]]
            if pv.brew_tags != brew_tags:
                pv.brew_tags = brew_tags
                pv.save()
                logger.info("Updated Product Version %s Brew Tags to %s", pv.name, pv.brew_tags)

        variants = self.get_paged("api/v1/variants", page_data_attr="data")
        for variant in variants:
            try:
                et_product_version = CollectorErrataProductVersion.objects.get(
                    et_id=variant["attributes"]["relationships"]["product_version"]["id"]
                )
            except CollectorErrataProductVersion.DoesNotExist:
                continue
            et_product_variant, created = CollectorErrataProductVariant.objects.get_or_create(
                et_id=variant["id"],
                name=variant["attributes"]["name"],
                cpe=variant["attributes"]["cpe"],
                product_version=et_product_version,
            )
            if created:
                logger.info("Created ET Product Variant: %s", et_product_variant.name)

    def get_erratum_components(self, erratum_id: str):
        """Fetch components attached to erratum and index by their Variant.

        Example:

        {
          "RHEL-7.6-AUS": {
            "name": "RHEL-7.6-AUS",
            "description": "Red Hat Enterprise Linux 7.6 Advanced Update Support",
            "builds": [
              {
                "polkit-0.112-18.el7_6.3": {
                  "nvr": "polkit-0.112-18.el7_6.3",
                  "nevr": "polkit-0:0.112-18.el7_6.3",
                  "id": 1848203,
                  "is_module": false,
                  "variant_arch": {
                    "7Server-7.6.AUS": {
                      "SRPMS": [
                        "polkit-0.112-18.el7_6.3.src.rpm"
                      ],
                      "x86_64": [
                        "polkit-0.112-18.el7_6.3.x86_64.rpm",
                        "polkit-0.112-18.el7_6.3.i686.rpm",
                        "polkit-devel-0.112-18.el7_6.3.x86_64.rpm",
                        "polkit-devel-0.112-18.el7_6.3.i686.rpm",
                        "polkit-debuginfo-0.112-18.el7_6.3.x86_64.rpm",
                        "polkit-debuginfo-0.112-18.el7_6.3.i686.rpm"
                      ],
                      "noarch": [
                        "polkit-docs-0.112-18.el7_6.3.noarch.rpm"
                      ]
                    }
                  },
                  "added_by": "jrybar"
                }
              }
            ]
          }
        }
        """
        variant_to_component_map = defaultdict(list)
        builds = self.get(f"api/v1/erratum/{erratum_id}/builds_list.json")
        for product_version in builds.values():
            for build in product_version["builds"]:
                for build_nvr, build_data in build.items():
                    build_id = build_data["id"]
                    for variant, components_by_arch in build_data["variant_arch"].items():
                        variant_components = []
                        for _, components in components_by_arch.items():
                            # Collect all components as a single list, their arch information is
                            # included in the NVRA string anyway.
                            variant_components.extend(components)
                        variant_to_component_map[variant].append({build_id: variant_components})

        return dict(variant_to_component_map)

    # TODO update this to use the ET Models
    def variant_cdn_repo_mapping(self) -> dict:
        """Fetches all existing Errata Tool Variants and the CDN repos they are configured with.

        Each erratum's builds are mapped against individual Variants that determine which repos
        the content gets pushed to.
        """
        variants = self.get_paged("api/v1/variants", page_data_attr="data")

        # Index a list of variants by their name so we can add repos to them, and pull out only
        # data that we need out of them.
        variants_with_repos = {}
        for variant in variants:
            variant_attrs = variant["attributes"]
            variants_with_repos[variant_attrs["name"]] = {
                "description": variant_attrs["description"],
                "cpe": variant_attrs["cpe"],
                "repos": [],
            }

        for repo in self.get_paged("api/v1/cdn_repos", page_data_attr="data"):
            repo_attrs = repo["attributes"]
            repo_rels = repo["relationships"]

            for variant in repo_rels["variants"]:
                variant_name = variant["name"]
                if variant_name not in variants_with_repos:
                    logger.warning(
                        "Repo %s links to a non-existent variant: %s",
                        repo_attrs["name"],
                        variant_name,
                    )
                    continue
                variants_with_repos[variant_name]["repos"].append(repo_attrs["name"])

        return variants_with_repos

    def normalize_erratum_id(self, name: str) -> int:
        if name.isdigit():
            return int(name)
        try:
            erratum_details = self.get(f"api/v1/erratum/{name}")
        except HTTPError as e:
            logger.error(e)
            return 0

        erratum_id = self.get_from_dict(erratum_details, ["content", "content", "errata_id"])
        if not erratum_id:
            return 0
        return int(erratum_id)

    # https://stackoverflow.com/questions/28225552/
    # is-there-a-recursive-version-of-the-dict-get-built-in/52260663#52260663
    def get_from_dict(self, data, keys):
        """Iterate nested dictionary"""
        try:
            return reduce(dict.get, keys, data)
        except TypeError:
            logger.warning("Didn't find %s in data", keys)
            return None

    def get_builds_for_errata(self, errata_id: int) -> list[int]:
        build_ids = set()
        for variant in self.get_erratum_components(str(errata_id)).values():
            for component in variant:
                build_ids.update(component.keys())
        return list(build_ids)

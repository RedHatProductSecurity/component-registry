import json
import logging
import typing
from collections import defaultdict

import requests
from django.conf import settings
from requests_gssapi import HTTPSPNEGOAuth

from corgi.collectors.brew import Brew
from corgi.collectors.models import (
    CollectorErrataProduct,
    CollectorErrataProductVariant,
    CollectorErrataProductVersion,
)
from corgi.core.models import SoftwareBuild

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
            et_product, created = CollectorErrataProduct.objects.update_or_create(
                et_id=product["id"],
                defaults={
                    "name": product["attributes"]["name"],
                    "short_name": product["attributes"]["short_name"],
                },
            )
            if created:
                logger.info("Created ET Product: %s", et_product.short_name)
            for product_version in product["relationships"]["product_versions"]:
                (
                    et_product_version,
                    created,
                ) = CollectorErrataProductVersion.objects.update_or_create(
                    et_id=product_version["id"],
                    defaults={"name": product_version["name"], "product": et_product},
                )
                if created:
                    logger.info("Created ET ProductVersion: %s", et_product_version.name)
        for pv in CollectorErrataProductVersion.objects.get_queryset():
            pv_details = self.get(f"api/v1/products/{pv.product.et_id}/product_versions/{pv.et_id}")
            brew_tags = [t.removesuffix("-candidate") for t in pv_details["data"]["brew_tags"]]
            if pv.brew_tags != brew_tags:
                pv.brew_tags = brew_tags
                pv.save()
                logger.info("Updated Product Version %s Brew Tags to %s", pv.name, pv.brew_tags)

        variants = self.get_paged("api/v1/variants", page_data_attr="data")
        for variant in variants:
            product_version_id = variant["attributes"]["relationships"]["product_version"]["id"]
            try:
                et_product_version = CollectorErrataProductVersion.objects.get(
                    et_id=product_version_id
                )
            except CollectorErrataProductVersion.DoesNotExist:
                logger.warning("Did not find product version with id %s", product_version_id)
                continue

            cpe = variant["attributes"]["cpe"]
            # CORGI-648 the value can be null
            if not cpe:
                cpe = ""
            et_product_variant, created = CollectorErrataProductVariant.objects.update_or_create(
                et_id=variant["id"],
                defaults={
                    "cpe": cpe,
                    "product_version": et_product_version,
                    "name": variant["attributes"]["name"],
                },
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
        variant_to_component_map: typing.DefaultDict[str, list] = defaultdict(list)
        builds = self.get(f"api/v1/erratum/{erratum_id}/builds_list.json")
        brew = Brew(SoftwareBuild.Type.BREW)
        for product_version in builds.values():
            for build in product_version["builds"]:
                for build_nvr, build_data in build.items():
                    if build_data["is_module"]:
                        self._parse_module(
                            build_nvr, build_data["variant_arch"], brew, variant_to_component_map
                        )
                        continue
                    build_id = build_data["id"]
                    for variant, components_by_arch in build_data["variant_arch"].items():
                        variant_components = []
                        for _, components in components_by_arch.items():
                            # Collect all components as a single list, their arch information is
                            # included in the NVRA string anyway.
                            variant_components.extend(components)
                        variant_to_component_map[variant].append({build_id: variant_components})

        return dict(variant_to_component_map)

    def save_variant_cdn_repo_mapping(self):
        """Fetches all existing Errata Tool Variants and the CDN repos they are configured with.

        Each erratum's builds are mapped against individual Variants that determine which repos
        the content gets pushed to.
        """

        for repo in self.get_paged("api/v1/cdn_repos", page_data_attr="data"):
            repo_attrs = repo["attributes"]
            repo_rels = repo["relationships"]

            for variant in repo_rels["variants"]:
                variant_name = variant["name"]
                try:
                    variant_obj = CollectorErrataProductVariant.objects.get(name=variant_name)
                except CollectorErrataProductVariant.DoesNotExist:
                    logger.debug(
                        "Repo %s links to a non-existent variant: %s",
                        repo_attrs["name"],
                        variant_name,
                    )
                    continue
                uniq_repos = set(variant_obj.repos)
                uniq_repos.add(repo_attrs["name"])
                variant_obj.repos = list(uniq_repos)
                logger.info("Linking repo %s to variant %s", repo_attrs["name"], variant_name)
                variant_obj.save()

    @staticmethod
    def get_variant_cdn_repo_mapping() -> dict[str, list[str]]:
        variants_with_repos = {
            variant.name: variant.repos
            for variant in CollectorErrataProductVariant.objects.exclude(repos__len=0)
        }
        return variants_with_repos

    def get_errata_key_details(self, name: str) -> tuple[int, bool]:
        erratum_details = self.get(f"api/v1/erratum/{name}")
        errata_type_details = list(erratum_details["errata"].values())
        if len(errata_type_details) != 1:
            raise ValueError(
                f"Erratum with name {name} has more than one type key: {errata_type_details}"
            )
        erratum_id = errata_type_details[0]["id"]
        shipped_live = errata_type_details[0]["status"] == "SHIPPED_LIVE"
        return erratum_id, shipped_live

    def get_builds_for_errata(self, errata_id: int) -> list[int]:
        build_ids = set()
        for variant in self.get_erratum_components(str(errata_id)).values():
            for component in variant:
                build_ids.update(component.keys())
        return list(build_ids)

    def _parse_module(
        self,
        module_name: str,
        module_data: dict[str, dict[str, list[str]]],
        brew: Brew,
        variant_to_component_map: typing.DefaultDict[str, list],
    ) -> None:
        """Persist collector models for modular builds from errata build_list.json. This allows
        later processing of modular builds in the Brew task slow_fetch_modular_build"""
        for variant, components_by_arch in module_data.items():
            modular_rpms = []
            for arch, components in components_by_arch.items():
                # SRPMS are looked up in persist_modules from Brew via RPMs
                if arch == "SRPMS":
                    continue
                # persist_modules expects the .rpm suffix stripped
                rpm_suffix_len = ".rpm"
                stripped_components = [
                    rpm[: -len(rpm_suffix_len)] for rpm in components if rpm.endswith(".rpm")
                ]
                modular_rpms.extend(stripped_components)
            build_ids = brew.persist_modules({module_name: modular_rpms})
            for build_id in build_ids:
                # persist_modules returns a flat list of build_ids, so its no longer possible to
                # map modular_rpms to build_ids. In slow_save_errata these are only saved as
                # meta_attr so, let's just save the entire list for each build_id
                variant_to_component_map[variant].append({build_id: modular_rpms})

    def get_erratum_notes(self, erratum_id: int) -> dict:
        # Get the contents of the "Notes" (formerly "How to test") field from an erratum.
        # Quarkus uses this field to associate an SBOM with an erratum.
        erratum_json = self.get(f"api/v1/erratum/{erratum_id}?format=json")
        try:
            # All products that use SBOMer should have notes set
            # NB: Though the field in the ET UI is called "Notes", the field name in the API
            # is "how_to_test", which was the original purpose of the Notes field.
            notes = json.loads(erratum_json["content"]["content"]["how_to_test"])
        except json.JSONDecodeError as error:
            logger.warning(f"Couldn't load Notes for erratum {erratum_id}")
            raise error
        return notes

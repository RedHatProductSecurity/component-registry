import json

import requests
from django.conf import settings
from requests_gssapi import HTTPSPNEGOAuth


class ProdDefs:
    # Do not use opportunistic_auth=True because it breaks the usage of VCR cassettes in tests,
    # and requires manual mock-patching of the below attribute.
    GSSAPI_AUTH = HTTPSPNEGOAuth()

    @classmethod
    def get_product_definitions_service(cls) -> dict:
        response = requests.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions",
            auth=cls.GSSAPI_AUTH,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def get_community_product_definitions(cls) -> dict:
        with open("config/community_product_definitions.json") as proddefs_data:
            return json.load(proddefs_data)

    @classmethod
    def load_products(cls, data: dict) -> list[dict]:
        products: list[dict] = []
        for ps_product, product in data["ps_products"].items():
            if settings.COMMUNITY_MODE_ENABLED:
                if product["business_unit"] != "Community":
                    continue
            else:  # Enterprise mode
                if product["business_unit"] == "Community":
                    continue
            product["id"] = ps_product
            products.append(product)

        return products

    @classmethod
    def load_product_definitions(cls) -> list:
        try:
            data = cls.get_product_definitions_service()
        except ConnectionError as e:
            if settings.COMMUNITY_MODE_ENABLED:
                data = cls.get_community_product_definitions()
            else:
                raise e

        products = cls.load_products(data)

        for product in products:
            print(f"product {product}")
            product_versions = []
            for ps_module in product["ps_modules"]:
                product_version = data["ps_modules"][ps_module]
                product_version["product_streams"] = []
                product_version["id"] = ps_module
                active_ps_update_streams = product_version["active_ps_update_streams"]
                for ps_update_stream in product_version["ps_update_streams"]:
                    product_stream = data["ps_update_streams"][ps_update_stream]
                    product_stream["id"] = ps_update_stream
                    if ps_update_stream in active_ps_update_streams:
                        product_stream["active"] = True
                    else:
                        product_stream["active"] = False
                    product_version["product_streams"].append(product_stream)
                product_versions.append(product_version)
            product["product_versions"] = product_versions

        return products

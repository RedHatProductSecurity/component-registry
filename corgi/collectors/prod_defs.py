import requests
from django.conf import settings
from requests_gssapi import HTTPSPNEGOAuth


class ProdDefs:
    # Do not use opportunistic_auth=True because it breaks the usage of VCR cassettes in tests,
    # and requires manual mock-patching of the below attribute.
    GSSAPI_AUTH = HTTPSPNEGOAuth()

    @classmethod
    def get_product_definitions(cls) -> dict:
        response = requests.get(
            f"{settings.PRODSEC_DASHBOARD_URL}/product-definitions",
            auth=cls.GSSAPI_AUTH,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def load_product_definitions(cls) -> list:
        data = cls.get_product_definitions()

        products = []
        for ps_product, product in data["ps_products"].items():
            if product["business_unit"] == "Community" and not settings.COMMUNITY_PRODUCTS_ENABLED:
                continue
            product["id"] = ps_product
            products.append(product)

        for product in products:
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

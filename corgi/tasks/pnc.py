import logging

import requests
from celery_singleton import Singleton
from requests.exceptions import HTTPError

from config.celery import app
from corgi.collectors.pnc import parse_pnc_sbom
from corgi.core.models import ProductVariant

logger = logging.getLogger(__name__)


@app.task(base=Singleton)
def slow_fetch_pnc_sbom(purl: str, product_data, build_data, sbom_data) -> None:
    logger.info("Fetching PNC SBOM %s for purl %s", sbom_data["id"], purl)
    """Fetch a PNC SBOM from sbomer"""

    # Validate the supplied product information
    try:
        ProductVariant.objects.get(name=product_data["productVariant"])
    except ProductVariant.DoesNotExist:
        logger.warning(
            "PNC SBOM provided for nonexistant product variant: %s", product_data["productVariant"]
        )
        return

    # Fetch and parse the SBOM
    try:
        r = requests.get(sbom_data["link"])
        r.raise_for_status()
    except HTTPError:
        logger.warning("Failed to fetch PNC SBOM: %s", sbom_data["link"])
        return

    # TODO: Fetch any necessary PNC build info

    logger.info(f"SBOM fetched for purl {purl}")

    parse_pnc_sbom(r.json())

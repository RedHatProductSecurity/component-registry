import logging
from json import JSONDecodeError

import requests
from celery_singleton import Singleton
from requests.exceptions import HTTPError

from config.celery import app
from corgi.collectors.cyclonedx import CycloneDxSbom
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
            "SBOM fetch request for nonexistant product variant: %s", product_data["productVariant"]
        )
        return

    # TODO: Fetch any necessary PNC build info

    # Fetch and parse the SBOM
    try:
        # TODO: Be paranoid and restrict the URL to a specific host or domain?
        r = requests.get(sbom_data["link"])
        r.raise_for_status()
    except HTTPError:
        logger.warning("SBOM fetch failed to fetch SBOM: %s", sbom_data["link"])
        return

    try:
        for component in CycloneDxSbom.parse(r.text):
            # Actual parsing of this SBOM to be added in CORGI-488
            pass
    except JSONDecodeError:  # TODO: Better named and more specific exceptions
        logger.warning("SBOM fetch failed to parse SBOM")
        return

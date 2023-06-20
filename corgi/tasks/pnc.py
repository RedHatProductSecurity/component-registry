import logging

import requests
from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.models import CollectorErrataProductVariant
from corgi.collectors.pnc import parse_pnc_sbom
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def slow_fetch_pnc_sbom(purl: str, product_data, build_data, sbom_data) -> None:
    logger.info("Fetching PNC SBOM %s for purl %s", sbom_data["id"], purl)
    """Fetch a PNC SBOM from sbomer"""

    # Validate the supplied product information
    try:
        CollectorErrataProductVariant.objects.get(name=product_data["productVariant"])
    except CollectorErrataProductVariant.DoesNotExist:
        logger.warning(
            "PNC SBOM provided for nonexistant product variant: %s", product_data["productVariant"]
        )
        return

    # Fetch and parse the SBOM
    response = requests.get(sbom_data["link"])
    response.raise_for_status()
    logger.info(f"SBOM fetched for purl {purl}")

    parse_pnc_sbom(response.json())

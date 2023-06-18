import json
import logging

logger = logging.getLogger(__name__)


def parse_pnc_sbom(data: str) -> int:
    """Parse an SBOM from PNC and create components
    Temporarily returns an int for testing purposes, to be
    completed in CORGI-488"""
    sbom = json.loads(data)
    return len(sbom["components"])

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def parse_pnc_sbom(data: Mapping[str, Any]) -> int:
    """Parse an SBOM from PNC and create components
    Temporarily returns an int for testing purposes, to be
    completed in CORGI-488"""
    if "components" not in data:
        raise ValueError("SBOM is missing component data")

    return len(data["components"])

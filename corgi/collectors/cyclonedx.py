import json
import logging

logger = logging.getLogger(__name__)


class CycloneDxSbom:
    """Parse a CycloneDX format SBOM and create components"""

    @classmethod
    def parse(cls, data: str):  # -> Iterator[dict[str, Any]]:
        contents = json.loads(data)
        return {"num_components": len(contents["components"])}  # TODO

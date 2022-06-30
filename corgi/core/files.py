import json
import logging

from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


class ProductManifestFile:
    """A data file that represents a product manifest in machine-readable SPDX / JSON format."""

    file_name = "product_manifest.json"  # Name of the Django template, not the final file itself.
    # TODO: Should be same template file for all products we want to manifest
    # We can subclass this to handle different Product subclasses with different properties
    # Or to handle different ways of generating manifest properties from Product properties

    def __init__(self, product) -> None:
        self.product = product  # Product (or subclass) model instance

    def render_content(self) -> str:
        kwargs_for_template = {"product": self.product}
        content = render_to_string(self.file_name, kwargs_for_template)

        return self._validate_and_clean(content)

    @staticmethod
    def _validate_and_clean(content) -> str:
        """Raise an exception if content for SPDX file is not valid JSON"""
        # The manifest template must use Django's escapejs filter,
        # to generate valid JSON and escape quotes + newlines
        # But this may output ugly Unicode like "\u000A",
        # so we convert from JSON back to JSON to get "\n" instead
        content = json.loads(content)
        return json.dumps(content, indent=2, sort_keys=True)

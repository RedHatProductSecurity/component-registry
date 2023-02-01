import json
import logging
from abc import ABC, abstractmethod

from django.template.loader import render_to_string

from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class ManifestFile(ABC):
    """A data file that represents a generic manifest in machine-readable SPDX / JSON format."""

    @property
    @abstractmethod
    def file_name(self) -> str:
        """Name of the Django template, not the final file itself."""
        pass

    def __init__(self, obj: TimeStampedModel) -> None:
        self.obj = obj  # Model instance to manifest (either Component or Product)

    def render_content(self) -> str:
        kwargs_for_template = {"obj": self.obj}
        content = render_to_string(self.file_name, kwargs_for_template)

        return self._validate_and_clean(content)

    @staticmethod
    def _validate_and_clean(content: str) -> str:
        """Raise an exception if content for SPDX file is not valid JSON"""
        # The manifest template must use Django's escapejs filter,
        # to generate valid JSON and escape quotes + newlines
        # But this may output ugly Unicode like "\u000A",
        # so we convert from JSON back to JSON to get "\n" instead
        content = json.loads(content)
        return json.dumps(content, indent=2, sort_keys=True)


class ComponentManifestFile(ManifestFile):
    """A data file that represents a component manifest in machine-readable SPDX / JSON format."""

    file_name = "component_manifest.json"  # Name of the Django template, not the final file itself.
    # We use the same template file for all components we want to manifest


class ProductManifestFile(ManifestFile):
    """A data file that represents a product manifest in machine-readable SPDX / JSON format."""

    file_name = "product_manifest.json"  # Name of the Django template, not the final file itself.
    # We use the same template file for all products we want to manifest
    # We can subclass this to handle different Product subclasses with different properties
    # Or to handle different ways of generating manifest properties from Product properties

    def render_content(self) -> str:

        latest_components = self.obj.get_latest_components()  # type: ignore

        kwargs_for_template = {
            "obj": self.obj,
            "latest_components": latest_components,
        }
        content = render_to_string(self.file_name, kwargs_for_template)

        return self._validate_and_clean(content)

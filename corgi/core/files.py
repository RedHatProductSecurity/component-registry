import json
import logging
import uuid
from abc import ABC, abstractmethod

import jsonschema
from django.conf import settings
from django.template.loader import render_to_string

from corgi.core.mixins import TimeStampedModel

logger = logging.getLogger(__name__)


class ManifestFile(ABC):
    """A data file that represents a generic manifest in machine-readable SPDX / JSON format."""

    # From https://raw.githubusercontent.com/spdx/spdx-spec/development/
    # v2.2.2/schemas/spdx-schema.json
    SCHEMA_FILE = settings.BASE_DIR / "corgi/web/static/spdx-22-schema.json"

    @property
    @abstractmethod
    def file_name(self) -> str:
        """Name of the Django template, not the final file itself."""
        pass

    def __init__(self, obj: TimeStampedModel) -> None:
        self.obj = obj  # Model instance to manifest (either Component or Product)

    def render_content(self) -> str:
        document_namespace = f"{self.obj.name.replace('/', '_')}-{self.obj.version}"
        kwargs_for_template = {"obj": self.obj, "namespace": document_namespace}
        content = render_to_string(self.file_name, kwargs_for_template)

        return self._validate_and_clean(content)

    @classmethod
    def _validate_and_clean(cls, content: str) -> str:
        """Raise an exception if content for SPDX file is not valid JSON / SPDX"""
        # The manifest template must use Django's escapejs filter,
        # to generate valid JSON and escape quotes + newlines
        # But this may output ugly Unicode like "\u000A",
        # so we convert from JSON back to JSON to get "\n" instead
        content = json.loads(content)

        with open(str(cls.SCHEMA_FILE), "r") as schema_file:
            schema = json.load(schema_file)
        jsonschema.validate(content, schema)

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
        components = self.obj.components  # type: ignore[attr-defined]
        # As this is a ProductManifestFile so we can assume self.obj has an ofuri
        released_components = components.manifest_components(ofuri=self.obj.ofuri)  # type: ignore[attr-defined] # noqa: E501
        distinct_provides = self.obj.provides_queryset  # type: ignore[attr-defined]
        distinct_upstreams = self.obj.upstreams_queryset  # type: ignore[attr-defined]
        cpes = self.obj.cpes  # type: ignore[attr-defined]
        external_name = self.obj.external_name  # type: ignore[attr-defined]
        kwargs_for_template = {
            "obj": self.obj,
            "external_name": external_name,
            "document_uuid": uuid.uuid4(),
            "released_components": released_components,
            "distinct_provides": distinct_provides,
            "distinct_upstreams": distinct_upstreams,
            "cpes": cpes,
        }

        content = render_to_string(self.file_name, kwargs_for_template)

        return self._validate_and_clean(content)

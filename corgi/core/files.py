import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime

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

    @staticmethod
    def get_created_at():
        dt = datetime.now()
        return dt.strftime("%Y-%m-%dT%H:%M:00Z")

    @staticmethod
    def get_document_uuid():
        return f"SPDXRef-{uuid.uuid4()}"

    def render_content(self, created_at: str = "", document_uuid: str = "") -> tuple[str, str, str]:
        if not created_at:
            created_at = self.get_created_at()
        document_namespace = (
            f"{self.obj.name.replace('/', '_')}-{self.obj.version}"  # type: ignore[attr-defined]
        )
        kwargs_for_template = {
            "obj": self.obj,
            "document_namespace": document_namespace,
            "created_at": created_at,
        }
        content = render_to_string(self.file_name, kwargs_for_template)

        return content, created_at, self.obj.pk


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

    def render_content(self, created_at: str = "", document_uuid: str = "") -> tuple[str, str, str]:
        components = self.obj.components  # type: ignore[attr-defined]
        # As this is a ProductManifestFile so we can assume self.obj has an ofuri
        released_components = components.manifest_components(ofuri=self.obj.ofuri)  # type: ignore[attr-defined] # noqa: E501
        distinct_provides = self.obj.provides_queryset  # type: ignore[attr-defined]
        distinct_upstreams = self.obj.upstreams_queryset  # type: ignore[attr-defined]
        cpes = self.obj.cpes  # type: ignore[attr-defined]
        external_name = self.obj.external_name  # type: ignore[attr-defined]
        if not created_at:
            created_at = self.get_created_at()
        if not document_uuid:
            document_uuid = f"SPDXRef-{uuid.uuid4()}"
        kwargs_for_template = {
            "obj": self.obj,
            "external_name": external_name,
            "document_uuid": document_uuid,
            "created_at": created_at,
            "released_components": released_components,
            "distinct_provides": distinct_provides,
            "distinct_upstreams": distinct_upstreams,
            "cpes": cpes,
        }

        content = render_to_string(self.file_name, kwargs_for_template)
        return content, created_at, document_uuid
